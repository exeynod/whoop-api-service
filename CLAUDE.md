# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

FastAPI proxy between the OpenClaw/Nanobot agent and the WHOOP Developer API v2. It
normalizes WHOOP's verbose, schema-drifting responses into compact, stable JSON contracts,
caches results on disk per profile, and handles OAuth token lifecycle. Deployed as an
internal-only Docker service (no published ports); agents reach it at
`http://whoop-service:8001` on the `nanobot-home_default` network.

`SKILL.md` is the agent-facing contract (endpoints, response shapes, call cadence, rate-limit
rules). Keep it in sync when you change route behavior or response fields — it is the source of
truth consumers rely on.

## Commands

```bash
# Local dev server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001

# Test suites (markers: unit / smoke / integration)
pytest -m "unit or smoke" --cov=app --cov-fail-under=85   # CI gate
pytest -m unit                                            # fast pure-logic tests
pytest -m smoke                                           # FastAPI TestClient HTTP tests
pytest tests/unit/test_whoop_client.py -k zone           # single file / -k filter
RUN_DOCKER_SMOKE=1 pytest tests/smoke/test_docker_smoke.py   # builds image, hits /health

# Live integration (manual, gated) — needs real tokens:
# copy tests/secrets/live_whoop.example.json -> tests/secrets/live_whoop.local.json, fill in, then:
pytest -m integration

# Deploy (internal network only)
docker compose up -d --build
```

`asyncio_mode = auto` is set in `pytest.ini`, so async tests need no decorator.

## Architecture

Request flow for a protected route:
`X-API-Key` header → `resolve_profile_name` (deps.py) maps key to a profile →
route handler (router.py) checks `FileCache` → on miss calls `WhoopClient` → client
ensures/refreshes the profile's OAuth token, fetches WHOOP collections, maps them to the
response contract → handler stamps `cached`/`timezone_offset` and writes back to cache.

- **`app/whoop_client.py`** — the bulk of the logic and the file you'll touch most. Owns
  per-profile token storage/refresh and all WHOOP response mapping. WHOOP payloads are
  treated as untrusted and shape-unstable: mapping goes through `_first_number` /
  `_first_number_from_blocks` helpers that try multiple candidate key names and tolerate
  missing fields. Records are correlated across collections by `score_state == "SCORED"`
  and by linking sleep → cycle (`cycle_id`) → recovery rather than by index. Day-level
  endpoints (`fetch_recovery`, `fetch_yesterday_snapshot`, `fetch_week_day`) compute MSK
  day bounds, convert to UTC, and pick the matching scored record; missing/unscored data
  returns a `pending` status rather than an error.
- **`app/router.py`** — data routes. Range routes (`/cycles`, `/workouts`,
  `/measurements/body/history`) require tz-aware `start`, enforce `MAX_RANGE_DAYS = 365`,
  and validate `next_token` as `YYYY-MM-DD` (pagination is date-cursor based, computed
  locally over the aggregated list — not passed through to WHOOP). Ranges longer than
  14 days are downsampled to weekly averages (`_should_rollup_weekly` → weekly aggregation).
  Note this rollup logic exists in **two** places: cycles roll up inside `whoop_client.py`,
  body-history rolls up in `router.py`.
- **`app/cache.py`** (`FileCache`) — JSON files under `/cache/<profile>/`. Only `status: "ready"`
  payloads are persisted. Two shapes: per-day files (`<endpoint>_<date>.json`) and range
  envelopes (`<endpoint>_range_<sha>.json` with `saved_at` + TTL). `/measurements/body`
  itself is never cached, but each `ready` fetch writes a dated body snapshot; those snapshots
  are the *only* source for `/measurements/body/history` (it makes no upstream call).
  `cleanup_expired` runs on startup and daily at 03:00 (APScheduler in `main.py` lifespan);
  body snapshots get 365-day retention, everything else `CACHE_RETENTION_DAYS` (30).
- **`app/rate_limiter.py`** — in-memory only, used solely by `/recovery/today`. When WHOOP
  returns `pending`, the payload is remembered and replayed for `WHOOP_MIN_INTERVAL_SECONDS`
  (300s) instead of re-hitting WHOOP. Cleared once a `ready` result is cached.
- **`app/deps.py`** — `get_settings`/`get_cache`/`get_rate_limiter`/`get_whoop_client` are
  `lru_cache` singletons. Tests (`conftest.py`) must `.cache_clear()` all of them between
  cases (already done in the autouse fixture).
- **`app/auth_router.py`** — OAuth2 authorization-code flow. `/auth/init?profile=<name>`
  encodes the profile into the OAuth `state`; `/auth/callback` parses it back and exchanges
  the code. See `docs/MANUAL_TOKEN_ISSUE.md` for issuing tokens without exposing the port.

## Coach (v2) layer

The normalized agent-facing contract lives in `/coach/*` (and `/raw/*`), layered
on top of the same transport/token/cache stack — the legacy routes above are kept
for backward compatibility.

- **`app/coach_normalize.py`** — pure, facts-only normalizers (recovery, sleep,
  day_strain, workout, body, freshness). Reuses `WhoopClient` static extractors
  (`_first_number`, `_extract_zone`, `_parse_datetime`, …) so coach output stays
  byte-consistent with `/cycles`/`/workouts`. Canonical v2 keys
  (`sleep_*_percentage`, `need_from_*_milli`, `user_calibrating` inside `score`);
  sleep assigned to **wake-date**; cycle **`is_final` from presence of `cycle.end`**;
  millis→hours (2 dp) / zone millis→minutes. Never emits coach flags/readiness.
- **`WhoopClient.fetch_coach_day` / `fetch_coach_range` / `fetch_coach_status`** —
  assembly methods. Each WHOOP collection is fetched independently and is
  partial-tolerant: a failing block degrades the response to `status=partial`
  with an `errors[]` entry while other blocks stay populated. Correlation reuses
  the existing `_pick_*` chain (sleep→cycle→recovery via `cycle_id`); soft
  fallbacks surface unscored recovery/sleep as `pending` not `missing`.
- **`app/coach_aggregate.py`** — pure week/training/sleep/recovery context math
  over the per-day rows from `fetch_coach_range`. Documented thresholds
  (high≥14, low<6, rest=no-workout, late-bedtime 00:30–06:00, strength sports);
  missing days skipped from averages; `strain_ratio_7d_vs_prev_7d` is `null` on
  divide-by-zero.
- **`app/coach_router.py`** — `/coach/*` routes. `/coach/today` and the aggregates
  are heartbeat-safe via the FileCache range cache with `require_ready=False` (so
  partial/pending days are also throttled); `refresh=true` bypasses. `today`
  exposes `today_strain`/`yesterday_strain` aliases. These routes return plain
  dicts (no `response_model`) to avoid silently dropping the variable partial shape.
- **`app/raw_router.py`** — `/raw/*` WHOOP passthrough; body field `next_token` is
  snake_case, request advances with `next_token` (forwarded as `nextToken`).

Coach correctness is pinned by `tests/unit/test_coach_normalize.py`,
`test_coach_assembler.py`, `test_coach_aggregates.py` and the smoke files
`test_coach_http_smoke.py` / `test_raw_http_smoke.py`. `SKILL.md` is the
agent-facing contract incl. surface-vs-drilldown rules — keep it in sync.

## Multi-profile auth model

`/secrets/whoop_tokens.json` (`ProfileTokenFile`, version 2) holds a `profiles` map; each
profile has its own WHOOP `TokenBundle`, an `api_token` (the value clients send as `X-API-Key`),
and `meta.active`. There is no global API key — `.env`'s key is unused. Important asymmetry:
the OAuth flow writes the WHOOP token bundle but saves `api_token` as `""` for new profiles
(`_save_profile_tokens`), so the `X-API-Key` value must be populated into the file manually
before a profile can authenticate data requests.

## Conventions

- Service timezone is `Europe/Moscow` (UTC+3), driven by `TZ`. "Today"/"yesterday"/day
  buckets are all computed in MSK, then converted to UTC for WHOOP queries.
- Error mapping is centralized in `router._whoop_error_response`: `ReauthorizationRequiredError`
  → 401; timeout/unavailable/unexpected-payload → 502. Bad `X-API-Key` → 401 from `deps`.
- HTTP logging to WHOOP is structured JSON with sensitive-value redaction
  (`_sanitize_mapping`); toggle via `WHOOP_HTTP_LOG_*` env vars.
- All token/cache writes are atomic (write `.tmp`, then `replace`).
- Config is env-only via pydantic-settings; copy `.env.example` to `.env`. Settings fields
  use `alias=` for the env-var name.
