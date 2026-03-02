---
name: whoop-api-service
description: Use this skill for requests about WHOOP recovery/sleep/strain data through the local Whoop Service proxy in profile-based multi-user mode. Trigger when user asks to fetch data from /recovery/today, /day/yesterday, /week.
metadata: {"openclaw":{"skillKey":"whoopApiService","requires":{"env":["WHOOP_SERVICE_BASE_URL","WHOOP_SERVICE_TOKEN"]},"primaryEnv":"WHOOP_SERVICE_TOKEN"}}
---

# Whoop API Service

Use this skill for any request that should go through this local service instead of calling WHOOP API directly.

## Required Environment

- `WHOOP_SERVICE_BASE_URL` (example: `http://127.0.0.1:8001`)
- `WHOOP_SERVICE_TOKEN` (profile API token used as value for header `X-API-Key`)
- Optional local label: `WHOOP_SERVICE_PROFILE` (human-readable profile name for operator context; not sent in request)

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
- `recovery sleep strain`
- `по моему профилю`
- `для пользователя`
- `по другому пользователю`
- `сравни профили`

## Route Map

Protected routes (must send `X-API-Key: ${WHOOP_SERVICE_TOKEN}`; token resolves active profile):

- `GET /recovery/today`
- `GET /day/yesterday`
- `GET /week`

## Call Rules

- Use only the service endpoints listed in this skill.
- Prefer `curl` for service calls.
- For protected routes always send header `X-API-Key`.
- Treat `X-API-Key` as profile selector: one request always runs in exactly one profile context.
- Never assume one token can represent multiple users.
- If user asks data for another profile/user, require that profile token explicitly before making calls.
- For profile comparison requests, run separate calls per profile token and clearly label results by profile.
- Do not send JSON body for these `GET` endpoints.

## Response Contracts

- `/recovery/today`:
  - `200 pending`: `{"status":"pending","reason":"..."}`
  - `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","recovery_score":0-100,"recovery_zone":"green|yellow|red","hrv_ms":<int>,"resting_hr_bpm":<int>,"cached":<bool>}`
- `/day/yesterday`:
  - `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","strain":{...},"sleep":{...},"cached":<bool>}`
- `/week`:
  - `200`: `{"period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"days":[{"status":"ready"| "missing", ...}]}`
- Errors:
  - `401`: invalid/missing API key, or token does not map to an active profile
  - `502`: upstream WHOOP issues or reauthorization required, payload `{"status":"error","reason":"...","detail":"..."?}`

## Behavior Notes (Important)

- Service timezone defaults to `Europe/Moscow`.
- Data isolation is profile-scoped:
  - `X-API-Key` resolves profile;
  - WHOOP OAuth tokens are used per resolved profile.
- `/recovery/today`:
  - caches only `ready` response;
  - replays latest `pending` within min interval (default 300s) without extra upstream call;
  - cache and pending window are isolated per profile.
- `/day/yesterday`:
  - returns yesterday relative to service timezone;
  - caches `ready` response per profile.
- `/week`:
  - returns 7-day window from `yesterday-6` to `yesterday`;
  - can include mix of `ready` and `missing` days;
  - cache is isolated per profile.

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
    "stages": {
      "deep_hours": 1.6,
      "rem_hours": 1.9,
      "light_hours": 3.2,
      "awake_hours": 0.7
    }
  },
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

## Minimal Execution Policy

- Usually perform one service call per user request.
- If user asks for a follow-up that needs additional calls, run only required next call(s).
- If protected endpoint returns `401`, ask user to provide/verify profile token (`WHOOP_SERVICE_TOKEN`) for the intended user.
- If endpoint returns `502` with reauthorization message, explicitly tell the user manual reauthorization is required for that profile and stop; do not attempt any autonomous actions.
