---
name: whoop-api-service
description: Use this skill for requests about WHOOP recovery/sleep/strain/workout/body measurements data through the local Whoop Service proxy. Trigger when user asks to fetch current recovery, sleep, strain, training history, or body measurements.
metadata: {"openclaw":{"skillKey":"whoopApiService","requires":{"env":["WHOOP_SERVICE_BASE_URL","WHOOP_SERVICE_TOKEN"]},"primaryEnv":"WHOOP_SERVICE_TOKEN"}}
---

# Whoop API Service (v2 coach layer)

Local proxy that normalizes WHOOP Developer API v2 into a coach-friendly data
layer. The service returns **facts only** — objective metrics, technical statuses
and freshness. It never returns `training_readiness`, `should_train`,
`coach_flags`, `recommendation` or any interpretation. **You** decide what the
numbers mean given the program, logs, pain and subjective readiness.

The primary contract is `/coach/*`. Old endpoints (`/recovery/today`,
`/day/yesterday`, `/week`, `/cycles`, `/workouts`, `/measurements/body`) and
`/raw/*` still exist for backward compatibility / debugging.

## Required Environment

- `WHOOP_SERVICE_BASE_URL` — service base URL; always use `${WHOOP_SERVICE_BASE_URL}`, never hardcode host/IP.
- `WHOOP_SERVICE_TOKEN` — value for header `X-API-Key`.

All `/coach/*` and `/raw/*` routes require `X-API-Key: ${WHOOP_SERVICE_TOKEN}`.

---

## Endpoints

### Primary (heartbeat / daily)
- `GET /coach/status` — service + WHOOP auth + per-block cache freshness + available_blocks.
- `GET /coach/today` — full coach-day object for the current Europe/Moscow date.
- `GET /coach/day/{YYYY-MM-DD}` — same object for a specific date.
- `GET /coach/body/latest` — latest body measurement (`ready` or explicit `missing`).

### Context / review
- `GET /coach/week?end=YYYY-MM-DD&days=7&include_days=true&include_workouts=true`
- `GET /coach/training-context?days=14&end=YYYY-MM-DD&include_daily=true&include_workouts=true`
- `GET /coach/sleep-context?days=14&end=YYYY-MM-DD`
- `GET /coach/recovery-context?days=14&end=YYYY-MM-DD`

### Raw (debugging only)
- `GET /raw/cycles|recoveries|sleeps|workouts?start=...&end=...&limit=25&next_token=...` — WHOOP passthrough.

### Query params (coach/today, coach/day)
- `include_raw=true|false` (default `false`) — add a `raw` block with original WHOOP payloads.
- `detail=surface|full` (default `full`) — `surface` drops heavy drilldown arrays (stage_summary, sleep_needed, hr_zones, zone_durations) but keeps the schema intact.
- `refresh=true|false` (default `false`) — bypass cache and refetch upstream.

---

## /coach/today shape

```json
{
  "status": "ready|partial|pending|missing|error",
  "date": "2026-05-31",
  "timezone": "Europe/Moscow",
  "day_start": "2026-05-31T00:00:00+03:00",
  "day_end": "2026-05-31T23:59:59+03:00",
  "generated_at": "2026-05-31T22:45:00+03:00",
  "freshness": { "recovery": {"status":"fresh|stale|missing|unknown","updated_at":"...","source":"whoop|cache|local"}, "sleep": {...}, "day_strain": {...}, "workouts_today": {...}, "body": {...} },
  "recovery": { "status":"ready", "score":74, "zone":"green", "hrv_ms":63, "resting_hr_bpm":48, "cycle_id":93845, "sleep_id":"...", "measured_at":"...", "...drilldown...": "spo2_percentage, skin_temp_celsius, user_calibrating, created_at, updated_at" },
  "sleep": { "status":"ready", "assigned_date":"2026-05-31", "assignment_rule":"wake_date", "nap":false, "started_at":"...", "ended_at":"...", "total_hours":6.4, "in_bed_hours":6.85, "efficiency_percentage":84, "performance_percentage":71, "consistency_percentage":42, "stages":{"deep_hours":0.9,"rem_hours":1.2,"light_hours":4.3,"awake_hours":0.45}, "...drilldown...":"stage_summary, sleep_needed, respiratory_rate" },
  "day_strain": { "status":"ready", "score":4.2, "is_final":false, "kilojoules":650, "average_hr_bpm":78, "max_hr_bpm":122, "...drilldown...":"cycle_start, cycle_end, hr_zones_available, hr_zones_min" },
  "previous_day_strain": { "status":"ready", "score":12.8, "is_final":true, "...": "..." },
  "workouts_today": [ {"workout_id":"...","sport_name":"volleyball","duration_min":105.0,"strain_score":10.4,"...drilldown...":"zone_durations_min, percent_recorded"} ],
  "workouts_yesterday": [],
  "body": { "status":"ready|missing", "weight_kg":83.2, "height_m":1.83, "max_heart_rate":195, "measured_at":"..." },
  "raw_refs": { "cycle_id":93845, "sleep_id":"...", "workout_ids":[], "body_measurement_id":null },
  "errors": [],
  "today_strain": "<alias of day_strain>",
  "yesterday_strain": "<alias of previous_day_strain>"
}
```

Key semantics:
- **Sleep is assigned to the wake date** (`assignment_rule=wake_date`): a sleep that ends this morning belongs to today even if it started yesterday.
- **`day_strain.is_final=false`** means the current cycle is still in progress; `previous_day_strain.is_final=true` is the closed prior day.
- **HR zones at the cycle/day level are not available from WHOOP** → `hr_zones_available=false`, `hr_zones_min=null` (explicit, not an error).
- **Partial responses**: if one block fails, `status=partial`, that block gets `status=error` with a `reason`, an entry is added to `errors[]`, and every other block is still returned. A missing body is `status=missing`, not an error.
- All timestamps are ISO8601 with timezone offset.

---

## Surface vs drilldown — when to look deeper

**Default: stay on surface metrics.** On an ordinary day use only surface fields
(recovery score/zone, HRV, RHR, sleep total/deep/REM, efficiency, today/yesterday
strain, workout count/strain) when recovery isn't red, sleep looks fine, there are
no complaints, strain isn't extreme, no key session/phase gate, no WHOOP-vs-subjective
conflict, and data is fresh and complete.

**Do a drilldown (read the `*_drilldown` fields already in the object — no extra call needed) when:**
- **Conflict** — green recovery but "foggy head"/no resource; green recovery but short/late/low-efficiency sleep; yellow/red recovery but great subjective state and a hard plan; low WHOOP strain after an obviously hard session; a workout with low `percent_recorded` or `score_state != SCORED`; normal WHOOP but CMJ/technique/feel dropped.
- **Hard training day** — heavy lower/plyo, intense volleyball, phase gate, CMJ/RSI/1RM test, return after a break/illness: check sleep timing, sleep need/debt, respiratory rate, yesterday strain + (workout) HR zones, freshness.
- **Pain ≥3/10 or unusual symptom** — recovery score/zone, HRV/RHR, sleep duration/deep/REM, yesterday strain, workouts in the last 48h, HR zones, duration, sport, freshness (to understand fatigue background, not to diagnose).
- **Weekly review** — `/coach/week` + daily rows + workouts + HR zones + recovery/sleep/strain trends.
- **Monthly / experiment (caffeine, diet break, work crunch)** — `/coach/sleep-context` and `/coach/recovery-context` and `/coach/training-context` over 14–30 days.

**Use `include_raw=true` only** when a normalized field looks wrong, to verify
mapping, when `score_state != SCORED`, on endpoint disagreement, or while
debugging. Not needed in a normal coaching cycle (`raw_refs` are always present).

**Cooldown** — daily surface every heartbeat; minimal drilldown 1–2× on a training
day before the session; full drilldown only on conflict/pain/review; raw only
manually or on a clear technical anomaly.

---

## Caching, freshness, rate limits

- `/coach/today` is **heartbeat-safe** (poll every 30–60 min): responses are cached per (date, detail, include_raw); `refresh=true` forces a refetch.
- Per-block TTLs: recovery/sleep stay fresh the whole day after ready; day_strain/workouts use a 45-min fresh/stale window; body ~12h. `stale` data is still returned but flagged.
- Week/context aggregates share a 45-min bundle cache.
- On `429`, back off ≥5s. On `502`, retry in ~10s.

## Error responses
- `401 {"status":"error","reason":"Unauthorized"}` — bad/missing `X-API-Key`.
- `401 {"status":"error","reason":"Reauthorization required"}` — manual WHOOP reauth needed; stop and tell the user.
- `429 {"status":"error","reason":"Too Many Requests","retry_after_seconds":10}`.
- `502 {"status":"error","reason":"...","detail":"safe, non-secret"}` — upstream issue.

## Trigger rules (RU)
Текущее восстановление, сон/нагрузка за вчера, сводка за неделю, история циклов,
тренировки/workouts, измерения тела (вес/рост). Для ежедневного heartbeat —
`/coach/today`; для разборов — `/coach/week` и `*-context`.

## What the agent still asks manually
Subjective readiness/«голова», боль 0–10 и где, необычные симптомы, рабочий
стресс, планы дня, фактические веса/повторы/RPE/RIR, техника/видео, питание, вес
из Huawei/YAZIO когда body `missing`. The service covers objective physiology and
load; it does not replace the subjective check-in.
