# Whoop Service

FastAPI proxy service between OpenClaw agent and WHOOP Developer API v2.

## Multi-User Roadmap

Step 1 is documented: profile-based multi-user architecture, where
`/secrets/whoop_tokens.json` stores multiple profiles and each profile has:

- its own Whoop OAuth token set;
- its own API token identity (for `X-API-Key` routing).

Technical design:
[docs/MULTI_USER_TECHNICAL_DESIGN.md](/Users/exy/pet_projects/whoop_api_service/docs/MULTI_USER_TECHNICAL_DESIGN.md)

## Features

- Profile-based API auth (`X-API-Key`) for all data routes
- OAuth2 authorization code flow (`/auth/init`, `/auth/callback`)
- Token persistence in `/secrets/whoop_tokens.json` with auto refresh per profile
- File cache in `/cache/<profile>/...` (only `ready` responses)
- Range cache for collection endpoints (`/cycles`, `/workouts`) with TTL
- Body measurements snapshot endpoint (`/measurements/body`)
- Synthetic body measurements history (`/measurements/body/history`) from local snapshots
- Range validation for collection routes with max depth 365 days
- Weekly rollup for long ranges (`>14` days) on `/cycles` and `/measurements/body/history`
- Daily cache cleanup on startup and at 03:00 MSK
- Local smoke tests, unit tests, and gated live integration tests

## Data Routes

Protected routes (require `X-API-Key`):

- `GET /recovery/today`
- `GET /day/yesterday`
- `GET /week`
- `GET /cycles?start=...&end=...&limit=...&next_token=...`
- `GET /workouts?start=...&end=...&limit=...&next_token=...`
- `GET /measurements/body`
- `GET /measurements/body/history?start=...&end=...&limit=...&next_token=...`

Collection routes (`/cycles`, `/workouts`) use `next_token` (snake_case).
Range routes (`/cycles`, `/workouts`, `/measurements/body/history`) enforce max depth `365` days.
For `/cycles` and `/measurements/body/history`, ranges longer than 14 days are downsampled to weekly averages.

## Error Semantics

- `401 Unauthorized`: invalid/missing `X-API-Key` or Whoop reauthorization required after refresh+retry.
- `502 Bad Gateway`: Whoop timeout/unavailable or unexpected upstream payload.

## Environment

Copy `.env.example` to `.env` and set values:

- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_REDIRECT_URI`
- `TZ=Europe/Moscow`
- `RANGE_READY_TTL_SECONDS=43200`
- `RANGE_PENDING_TTL_SECONDS=300`
- `WHOOP_HTTP_LOG_ENABLED=true`
- `WHOOP_HTTP_LOG_LEVEL=INFO`
- `WHOOP_HTTP_LOG_BODY_MAX_CHARS=4000`
- `WHOOP_HTTP_LOG_REDACT_SENSITIVE=true`
- `WHOOP_HTTP_LOG_FILE_DIR=/tmp`

`X-API-Key` is resolved from profile records in `/secrets/whoop_tokens.json`.
Global API key in `.env` is not used.

## Curl Examples

Cycles:

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/cycles?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Workouts:

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/workouts?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Body measurements snapshot:

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/measurements/body" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Body measurements history:

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/measurements/body/history?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

## Local Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8001
```

## Test Commands

Unit + smoke with coverage gate:

```bash
pytest -m "unit or smoke" --cov=app --cov-fail-under=85
```

Smoke only:

```bash
pytest -m smoke
```

Optional container smoke (build + `/health` check):

```bash
RUN_DOCKER_SMOKE=1 pytest -m smoke tests/smoke/test_docker_smoke.py
```

Live integration (manual gated):

1. Create `tests/secrets/live_whoop.local.json` from `tests/secrets/live_whoop.example.json`
2. Fill in real `access_token` and `whoop_user_id`
3. Run:

```bash
pytest -m integration
```

## Docker Deploy

Internal network only (no public ports by default):

```bash
docker compose up -d --build
```

Manual issuance of WHOOP tokens (without exposing service port) is documented here:
[docs/MANUAL_TOKEN_ISSUE.md](/Users/exy/pet_projects/whoop_api_service/docs/MANUAL_TOKEN_ISSUE.md)
