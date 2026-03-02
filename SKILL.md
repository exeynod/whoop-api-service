---
name: whoop-api-service
description: Use this skill for requests about WHOOP recovery/sleep/strain/workout/body measurements data through the local Whoop Service proxy. Trigger when user asks to fetch current recovery, sleep, strain, training history, or body measurements.
metadata: {"openclaw":{"skillKey":"whoopApiService","requires":{"env":["WHOOP_SERVICE_BASE_URL","WHOOP_SERVICE_TOKEN"]},"primaryEnv":"WHOOP_SERVICE_TOKEN"}}
---

# Whoop API Service

Use this skill for any request that should go through this local service instead of calling WHOOP API directly.

## Required Environment

- `WHOOP_SERVICE_BASE_URL` (required service base URL; use only `${WHOOP_SERVICE_BASE_URL}`, never hardcode host or IP)
- `WHOOP_SERVICE_TOKEN` (value for header `X-API-Key`)

---

## Use Cases & Recommended Calls

### Daily Heartbeat (07:00 MSK) — 2 calls
**Purpose**: Check current recovery and previous day's sleep/strain

```bash
# Call 1: Current recovery
curl -sS "${WHOOP_SERVICE_BASE_URL}/recovery/today" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"

# Call 2: Yesterday's data
curl -sS "${WHOOP_SERVICE_BASE_URL}/day/yesterday" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

**Timing**: Back-to-back OK, no sleep needed  
**Expect**: Instant responses (cached)

---

### Weekly Review (Sunday) — 2 calls
**Purpose**: Overview of 7 days + current cycle metrics

```bash
# Call 1: 7-day snapshot
curl -sS "${WHOOP_SERVICE_BASE_URL}/week" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"

# Call 2: Weekly rollup (1 point per week)
curl -sS "${WHOOP_SERVICE_BASE_URL}/cycles?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T23:59:59%2B03:00&limit=25" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

**Timing**: 2-3 sec sleep between calls (rate limit safety)  
**Expect**: `/week` instant, `/cycles` may have pagination (next_token)

---

### Monthly Analysis (1st of month) — 3 calls with pagination
**Purpose**: Full month history + workouts + measurements

```bash
# Call 1: Get cycles for the month
curl -sS "${WHOOP_SERVICE_BASE_URL}/cycles?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T23:59:59%2B03:00&limit=25" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"

# If response has next_token, sleep 5 sec then call:
sleep 5
curl -sS "${WHOOP_SERVICE_BASE_URL}/cycles?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T23:59:59%2B03:00&limit=25&next_token=<TOKEN>" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"

# Call 2: Get workouts for the month (usually 25 per page)
sleep 5
curl -sS "${WHOOP_SERVICE_BASE_URL}/workouts?start=2026-02-01T00:00:00%2B03:00&end=2026-03-02T23:59:59%2B03:00&limit=25" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"

# If workouts has next_token, paginate with 5 sec sleep between calls

# Call 3: Current body measurements
sleep 5
curl -sS "${WHOOP_SERVICE_BASE_URL}/measurements/body" \
  -H "X-API-Key: ${WHOOP_SERVICE_TOKEN}"
```

**Timing**: Sleep 5 sec **between each call**  
**Expect**: Cycles may need pagination (next_token), workouts will need pagination

---

## Route Map

Protected routes (must send `X-API-Key: ${WHOOP_SERVICE_TOKEN}`):

- `GET /recovery/today` — Current recovery (cached, updates after sleep)
- `GET /day/yesterday` — Yesterday's strain & sleep (cached)
- `GET /week` — 7-day snapshot (cached)
- `GET /cycles` — Recovery/HRV/Sleep history with pagination (cached)
- `GET /workouts` — Training history with pagination (cached)
- `GET /measurements/body` — Current weight/height/max HR (live, no cache)
- `GET /measurements/body/history` — Historical measurements with pagination (local snapshots only)

---

## Call Rules

- Use only the service endpoints listed in this skill.
- Prefer `curl` for service calls.
- Always call endpoints via `${WHOOP_SERVICE_BASE_URL}`; never hardcode `127.0.0.1` or any fixed host.
- For protected routes always send header `X-API-Key`.
- Do not send JSON body for these `GET` endpoints.
- For collection pagination use only `next_token` (snake_case).
- For `/cycles`, `/workouts`, `/measurements/body/history`, `start` is required and must include timezone offset (ISO8601 datetime).
- For `/cycles` and `/measurements/body/history`, `next_token` (if present) must be `YYYY-MM-DD`.
- For `/cycles`, `/workouts`, `/measurements/body/history`, keep `limit` in `1..25` (recommend `limit=25` for months, `limit=10` for weeks).
- For `/cycles`, `/workouts`, `/measurements/body/history`, enforce range depth `<= 365 days` (`end - start`).
- For `/cycles` and `/measurements/body/history`, if range is over 14 days, expect weekly averaged output (roughly one point per week).

---

## Rate Limiting

**Service enforces rate limits** — if you get `429 Too Many Requests`:

- **Between pagination calls**: Sleep 5 seconds minimum
- **Between different endpoints**: Sleep 2-3 seconds minimum
- **Heartbeat (2 calls)**: No sleep needed, calls are sequential
- **Monthly analysis (3+ calls)**: Sleep 5 seconds between each

**Strategy**:
- Heartbeat: 2 calls back-to-back (fast, <1s total)
- Weekly: 2 calls with 3 sec sleep (safe)
- Monthly: Loop with 5 sec sleep between each paginated call

---

## Response Contracts

### `/recovery/today`
- `200 pending`: `{"status":"pending","reason":"..."}`
- `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","recovery_score":0-100,"recovery_zone":"green|yellow|red","hrv_ms":<int>,"resting_hr_bpm":<int>,"spo2_percentage"?:<float>,"skin_temp_celsius"?:<float>,"user_calibrating"?:<bool>,"timezone_offset":"+03:00","cached":<bool>}`

### `/day/yesterday`
- `200 ready`: `{"status":"ready","date":"YYYY-MM-DD","strain":{"score":<float>,"kilojoules":<int>,"avg_hr_bpm":<int>,"max_hr_bpm":<int>},"sleep":{"score":<int>,"total_hours":<float>,"consistency_percentage"?:<int>,"efficiency_percentage"?:<int>,"stages":{"deep_hours":<float>,"rem_hours":<float>,"light_hours":<float>,"awake_hours":<float>}},"timezone_offset":"+03:00","cached":<bool>}`

### `/week`
- `200`: `{"period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"days":[{"date":"YYYY-MM-DD","status":"ready|missing","recovery_score":<int>,"recovery_zone":"green|yellow|red","hrv_ms":<int>,"resting_hr_bpm":<int>,"strain_score":<float>,"sleep_score":<int>,"sleep_hours":<float>}]}`

### `/cycles`
- `200 ready`: `{"status":"ready","period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"days":[{"date":"YYYY-MM-DD","recovery_score":<int>,"recovery_zone":"green|yellow|red","hrv_ms":<int>,"resting_hr_bpm":<int>,"strain_score":<float>,"sleep_score":<int>,"sleep_hours":<float>,"sleep_consistency_percentage":<int>}],"next_token":<string|null>,"timezone_offset":"+03:00","cached":<bool>}`

### `/workouts`
- `200 ready`: `{"status":"ready","period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"workouts":[{"workout_id":"...","date":"YYYY-MM-DD","sport_name":"volleyball|ice-hockey|weightlifting|functional-fitness|...","strain_score":<float>,"average_hr_bpm":<int>,"max_hr_bpm":<int>,"kilojoules":<int>,"zone_durations":{"zone_zero_milli":<int>,"zone_one_milli":<int>,"zone_two_milli":<int>,"zone_three_milli":<int>,"zone_four_milli":<int>,"zone_five_milli":<int>}}],"next_token":<string|null>,"timezone_offset":"+03:00","cached":<bool>}`

### `/measurements/body`
- `200 ready`: `{"status":"ready","measured_at":"ISO8601","height_meter":<float>,"weight_kilogram":<float>,"max_heart_rate":<int>,"timezone_offset":"+03:00","cached":false}`
- `200 pending`: `{"status":"pending","reason":"Body measurements are not available yet."}`

### `/measurements/body/history`
- `200 ready`: `{"status":"ready","period":{"from":"YYYY-MM-DD","to":"YYYY-MM-DD"},"measurements":[{"date":"YYYY-MM-DD","measured_at":"ISO8601","height_meter"?:<float>,"weight_kilogram"?:<float>,"max_heart_rate"?:<int>}],"next_token":<string|null>,"timezone_offset":"+03:00","cached":true}`
- `200 pending`: `{"status":"pending","reason":"Body measurements are not available yet."}`

### Error Responses
- `401`: `{"detail":"Unauthorized"}` — invalid/missing API key
- `401`: `{"status":"error","reason":"Reauthorization required"}` — manual reauth needed in Whoop app
- `429`: `{"detail":"Too Many Requests"}` — rate limit hit, wait 5+ sec
- `502`: `{"status":"error","reason":"Unexpected Whoop response","detail":"..."}` — upstream error, retry with 10 sec delay

---

## Behavior Notes

- **Service timezone**: Europe/Moscow (UTC+3)
- **Range limit**: Max 365 days per request (`end - start <= 365 days`)
- **Weekly rollup**: For ranges > 14 days, returns ~1 point per week (averaged metrics)
- **Caching**:
  - `/recovery/today`: Caches `ready` only, replays `pending` without upstream call
  - `/day/yesterday`: Caches all `ready` responses
  - `/week`: Caches 7-day snapshot
  - `/cycles`, `/workouts`: Range-cached (TTL by service config)
  - `/measurements/body`: **No cache**, always fresh
  - `/measurements/body/history`: Local snapshots only, no upstream calls
- **Pagination**: Use `next_token` if present (not null), follow with same params + `&next_token=<TOKEN>`

---

## Trigger Rules (RU)

Use this skill when the request is about:

- Current recovery (восстановление сегодня, recovery сегодня, какой recovery)
- Sleep/strain data (сон за вчера, нагрузка за вчера, strain)
- Weekly overview (сводка за неделю, статистика за неделю)
- Historical cycles (циклы за период, история за месяц)
- Training history (тренировки, workouts, какие были тренировки)
- Body measurements (вес, рост, измерения тела, weight, height)

---

## Minimal Execution Policy

- **Heartbeat (daily)**: Execute exactly 2 calls (recovery/today + day/yesterday), expect instant cached responses
- **Weekly**: Execute week + cycles (2 calls), handle pagination if next_token present
- **Monthly**: Execute cycles + workouts + measurements/body in sequence, handle pagination with 5 sec sleep
- If user asks for follow-up, run only required next call(s)
- If `401 Unauthorized`: ask user to verify `WHOOP_SERVICE_TOKEN`
- If `401 Reauthorization required`: tell user manual Whoop app reauth is required, stop
- If `429`: wait 5-10 seconds and retry (automatic backoff in exec)
- If `502`: log error, suggest retry in 10 seconds (upstream issue)
