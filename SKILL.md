---
name: whoop-api-service
description: Use this skill for requests about WHOOP recovery/sleep/strain/workout data through the local Whoop Service proxy. Trigger when user asks to fetch data from /recovery/today, /day/yesterday, /week, /cycles, /workouts.
metadata: {"openclaw":{"skillKey":"whoopApiService","requires":{"env":["WHOOP_SERVICE_BASE_URL","WHOOP_SERVICE_TOKEN"]},"primaryEnv":"WHOOP_SERVICE_TOKEN"}}
---

# Whoop API Service

Use this skill for any request that should go through this local service instead of calling WHOOP API directly.

## Required Environment

- `WHOOP_SERVICE_BASE_URL` (required service base URL; use only `${WHOOP_SERVICE_BASE_URL}`, never hardcode host or IP)
- `WHOOP_SERVICE_TOKEN` (value for header `X-API-Key`)

## Trigger Rules (RU)

Use this skill when the request is about WHOOP data via this service.

Trigger words and phrases (Russian):

- `восстановление сегодня`
- `recovery за сегодня`
- `восстановление вчера`
- `сон и нагрузка за вчера`
- `сон за сегодня`
- `сводка за неделю`
- `недельная статистика whoop`
- `нагрузка за неделю`
- `strain за вчера`
- `sleep за вчера`
- `история циклов whoop`
- `циклы за месяц`
- `тренировки whoop`
- `workouts за период`
- `recovery sleep strain`

## Route Map

Protected routes (must send `X-API-Key: ${WHOOP_SERVICE_TOKEN}`):

- `GET /recovery/today`
- `GET /day/yesterday`
- `GET /week`
- `GET /cycles`
- `GET /workouts`

## Call Rules

- Use only the service endpoints listed in this skill.
- Prefer `curl` for service calls.
- Always call endpoints via `${WHOOP_SERVICE_BASE_URL}`; never hardcode `127.0.0.1` or any fixed host.
- For protected routes always send header `X-API-Key`.
- Do not send JSON body for these `GET` endpoints.
- For collection pagination use only `next_token` (snake_case).
- For `/cycles` and `/workouts`, `start` is required and must include timezone offset (ISO8601 datetime).
- For `/cycles`, `next_token` (if present) must be `YYYY-MM-DD`.
- For `/cycles` and `/workouts`, keep `limit` in `1..25` (default `10`).

## Response Contracts

- `/recovery/today`:
  - `200 pending`: `{"status":"pending","reason":"..."}`
  - `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","recovery_score":0-100,"recovery_zone":"green|yellow|red","hrv_ms":<int>,"resting_hr_bpm":<int>,"spo2_percentage"?:<float>,"skin_temp_celsius"?:<float>,"user_calibrating"?:<bool>,"timezone_offset":"+03:00","cached":<bool>}`
- `/day/yesterday`:
  - `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","strain":{...},"sleep":{"disturbance_count"?:<int>,"sleep_cycle_count"?:<int>,"consistency_percentage"?:<int>,"efficiency_percentage"?:<int>,"sleep_needed_hours"?:<float>,"sleep_debt_hours"?:<float>,"strain_related_need_hours"?:<float>,...},"timezone_offset":"+03:00","cached":<bool>}`
- `/week`:
  - `200`: `{"period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"days":[{"status":"ready"| "missing", ...}]}`
- `/cycles`:
  - `200 ready`: `{"status":"ready","period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"days":[...],"next_token":<string|null>,"timezone_offset":"+03:00","cached":<bool>}`
- `/workouts`:
  - `200 ready`: `{"status":"ready","period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"workouts":[{"sport_name":"...","zone_durations"?:{...},...}],"next_token":<string|null>,"timezone_offset":"+03:00","cached":<bool>}`
- Errors:
  - `401`: invalid/missing API key (`{"detail":"Unauthorized"}`)
  - `401`: reauthorization required (`{"status":"error","reason":"Reauthorization required"}`)
  - `502`: upstream WHOOP timeout/unavailable or unexpected payload, payload `{"status":"error","reason":"...","detail":"..."?}`

## Behavior Notes (Important)

- Service timezone defaults to `Europe/Moscow`.
- `/recovery/today`:
  - caches only `ready` response;
  - replays latest `pending` within min interval (default 300s) without extra upstream call.
- `/day/yesterday`:
  - returns yesterday relative to service timezone;
  - caches `ready` response.
- `/week`:
  - returns 7-day window from `yesterday-6` to `yesterday`;
  - can include mix of `ready` and `missing` days.
- `/cycles`, `/workouts`:
  - support `start`, `end`, `limit`, `next_token`;
  - return `ready` with `cached` flag and `timezone_offset`;
  - use range-cache (TTL by service config).

## Curl Templates

Scenario (RU request): `Покажи восстановление за сегодня` or `Какой recovery сегодня?`

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/recovery/today" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Example response (`ready`):

```json
{
  "status": "ready",
  "date": "2026-03-02",
  "recovery_score": 74,
  "recovery_zone": "yellow",
  "hrv_ms": 52,
  "resting_hr_bpm": 48,
  "spo2_percentage": 96.5,
  "skin_temp_celsius": 33.8,
  "user_calibrating": false,
  "timezone_offset": "+03:00",
  "cached": false
}
```

Example response (`pending`):

```json
{
  "status": "pending",
  "reason": "Sleep not yet complete. Recovery will be available after wake."
}
```

Scenario (RU request): `Дай сон и нагрузку за вчера` or `Покажи вчерашний день`

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/day/yesterday" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Example response:

```json
{
  "status": "ready",
  "date": "2026-03-01",
  "strain": {
    "score": 14.2,
    "kilojoules": 1823,
    "avg_hr_bpm": 112,
    "max_hr_bpm": 171
  },
  "sleep": {
    "score": 81,
    "total_hours": 7.4,
    "performance_percent": 88,
    "respiratory_rate": 15.2,
    "disturbance_count": 3,
    "sleep_cycle_count": 5,
    "consistency_percentage": 92,
    "efficiency_percentage": 89,
    "sleep_needed_hours": 7.5,
    "sleep_debt_hours": 0.2,
    "strain_related_need_hours": 0.5,
    "stages": {
      "deep_hours": 1.6,
      "rem_hours": 1.9,
      "light_hours": 3.2,
      "awake_hours": 0.7
    }
  },
  "timezone_offset": "+03:00",
  "cached": true
}
```

Scenario (RU request): `Собери недельную сводку` or `Покажи статистику за неделю`

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/week" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Example response:

```json
{
  "period": {
    "from": "2026-02-23",
    "to": "2026-03-01"
  },
  "days": [
    {
      "date": "2026-02-23",
      "status": "ready",
      "recovery_score": 70,
      "recovery_zone": "yellow",
      "hrv_ms": 50,
      "resting_hr_bpm": 49,
      "strain_score": 12.0,
      "sleep_score": 82,
      "sleep_hours": 7.1
    },
    {
      "date": "2026-02-24",
      "status": "missing"
    }
  ]
}
```

Scenario (RU request): `Покажи циклы за период` or `История циклов за месяц`

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/cycles?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Scenario (RU request): `Покажи тренировки за период` or `Какие были workouts`

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/workouts?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

Follow-up page (only if previous response has non-null `next_token`):

```bash
curl -sS "${WHOOP_SERVICE_BASE_URL}/workouts?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T00:00:00%2B03:00&limit=10&next_token=<TOKEN_FROM_PREVIOUS_RESPONSE>" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

## Minimal Execution Policy

- Usually perform one service call per user request.
- If user asks for a follow-up that needs additional calls, run only required next call(s).
- If protected endpoint returns `401` with `{"detail":"Unauthorized"}`, ask user to provide/verify `WHOOP_SERVICE_TOKEN`.
- If endpoint returns `401` with `reason="Reauthorization required"`, explicitly tell the user manual reauthorization is required and stop; do not attempt any autonomous actions.
