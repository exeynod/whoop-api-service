# ТЗ: Whoop Service

## Контекст

Whoop Service — автономный микросервис, часть системы персонального тренировочного ассистента.
Является прокси-слоем между агентом OpenClaw и Whoop API.

Агент **никогда не обращается к Whoop напрямую** — только через этот сервис.

Сервис разворачивается в Docker-контейнере на VPS рядом с OpenClaw.
Оба контейнера находятся в одной Docker internal network и общаются по внутренним именам сервисов.
Сервис **не публикует порты наружу** — доступен только внутри Docker network.

---

## Стек

- Python + FastAPI
- Docker-контейнер
- Файловый кэш (JSON)
- Таймзона: **MSK (UTC+3)** — все операции с датами в этой таймзоне

---

## Аутентификация

### Между агентом и сервисом
Заголовок `X-API-Key` со статичным секретом из `.env`.
Все эндпоинты кроме `/health` и `/auth/*` требуют валидный ключ.
При невалидном ключе — `401 Unauthorized`.

### Между сервисом и Whoop API
OAuth2 Authorization Code Flow.
`access_token` и `refresh_token` хранятся в `/secrets/whoop_tokens.json`.
Сервис автоматически обновляет `access_token` через `refresh_token` при истечении.
Если `refresh_token` протух или Whoop возвращает `401` после refresh+retry — все эндпоинты данных возвращают `401` с `reason: "Reauthorization required"`.
Требуется ручное переподключение через `/auth/init`.

Credentials (`client_id`, `client_secret`) — из `.env`.
Запрашиваемый scope: `offline read:recovery read:sleep read:cycles read:workout read:profile read:body_measurement`.

### Первичная авторизация (первый запуск)

При первом деплое токенов ещё нет. Последовательность:

1. Временно открыть порт сервиса наружу (через `docker-compose` или firewall)
2. Открыть в браузере: `http://<VPS_IP>:<PORT>/auth/init`
3. Сервис редиректит на Whoop OAuth страницу
4. После подтверждения Whoop редиректит на `/auth/callback`
5. Сервис сохраняет токены в `/secrets/whoop_tokens.json`
6. Закрыть внешний доступ к порту — дальнейшее общение только через Docker internal network

Эндпоинты `/auth/init` и `/auth/callback` не требуют `X-API-Key`.

---

## Кэширование

- Кэш хранится в `/cache/` в виде JSON-файлов
- Формат имени: `{endpoint}_{YYYY-MM-DD}.json` (дата в MSK), например `recovery_2026-02-27.json`
- **Кэшируется только статус `ready`** — `pending` и `error` никогда не кэшируются
- При первом успешном ответе (`ready`) за текущий день — результат сохраняется в кэш
- Все последующие запросы за тот же день — читаются из кэша без обращения к Whoop API
- Данные за прошлые дни (`/day/yesterday`, `/week`) — кэшируются навсегда (ретроспективные данные не меняются)
- `/week` использует пообъектный кэш: каждый из 7 дней кэшируется отдельным файлом. При запросе сервис собирает ответ из кэша, запрашивая у Whoop только те дни, которых нет в кэше
- `/cycles` и `/workouts` используют range-кэш по ключу `start|end|limit|next_token` с TTL:
  - `RANGE_READY_TTL_SECONDS` (по умолчанию `43200`)
  - `RANGE_PENDING_TTL_SECONDS` зарезервирован (по умолчанию `300`)
- `/measurements/body` не использует cache-read; при `ready` сохраняется snapshot в `body_measurement_YYYY-MM-DD.json`
- `/measurements/body/history` читает только локальные snapshots (synthetic history)

### Очистка кэша
Файлы старше retention удаляются по двум триггерам:
- Cron внутри контейнера: раз в сутки в 03:00 MSK
- При старте сервиса (дополнительно)

Retention:
- `body_measurement_*`: 365 дней
- остальные date-cache файлы: 30 дней

---

## Таймауты

- Запрос к Whoop API: **10 секунд**
- Проверка `whoop_reachable` в `/health`: **3 секунды** (не блокирует основной ответ `/health`)
- При превышении таймаута — `502` с `reason: "Whoop API timeout"`

---

## Rate Limiting (защита от избыточных запросов к Whoop)

Сервис не обращается к Whoop API чаще чем **раз в 5 минут** на один эндпоинт.
Если агент делает retry при `pending` чаще — сервис возвращает последний `pending` ответ без нового запроса к Whoop.

---

## Обработка ошибок

| Ситуация | HTTP | `status` | Примечание |
|---|---|---|---|
| Данные успешно получены | `200` | `ready` | |
| Сон ещё не завершён | `200` | `pending` | Агент делает retry позже |
| Whoop API недоступен / таймаут | `502` | `error` | |
| Refresh token протух / reauth required | `401` | `error` | `reason: "Reauthorization required"` |
| Невалидный API Key | `401` | — | |
| Whoop вернул неожиданный формат | `502` | `error` | `reason: "Unexpected Whoop response"` |

**Кэш никогда не используется как fallback при ошибке.**

---

## Эндпоинты

### `GET /health`

Не требует аутентификации.
Проверка `whoop_reachable` — лёгкий ping с таймаутом 3 секунды, не блокирует ответ.

**Response `200`:**
```json
{
  "status": "ok",
  "whoop_reachable": true,
  "tokens_valid": true
}
```

Поле `tokens_valid: false` — токены отсутствуют или протухли, нужна повторная авторизация.

---

### `GET /auth/init`

Не требует аутентификации. Только для первичной авторизации.
Редиректит браузер на Whoop OAuth страницу.

---

### `GET /auth/callback`

Не требует аутентификации. Только для первичной авторизации.
Принимает callback от Whoop, сохраняет токены в `/secrets/whoop_tokens.json`.

**Response `200`:**
```json
{
  "status": "authorized",
  "message": "Tokens saved. You can close this tab."
}
```

---

### `GET /recovery/today`

Recovery текущего дня по MSK.

**Логика:**
1. Проверить кэш на сегодня — если есть `ready`, вернуть из кэша
2. Проверить rate limit — если запрос к Whoop был менее 5 минут назад, вернуть последний `pending`
3. Запросить Whoop API
4. Если данные готовы — сохранить в кэш, вернуть `ready`
5. Если сон не завершён — вернуть `pending` (не кэшировать)
6. Если Whoop недоступен — вернуть `502`

**Response `200` — `ready`:**
```json
{
  "status": "ready",
  "date": "2026-02-27",
  "recovery_score": 74,
  "recovery_zone": "yellow",
  "hrv_ms": 52,
  "resting_hr_bpm": 48,
  "spo2_percentage": 96.5,
  "skin_temp_celsius": 33.8,
  "user_calibrating": false,
  "timezone_offset": "+03:00",
  "cached": true
}
```

`recovery_zone` — напрямую из Whoop: `green` / `yellow` / `red`.
`cached` — `true` если из кэша, `false` если свежий запрос к Whoop.

**Response `200` — `pending`:**
```json
{
  "status": "pending",
  "reason": "Sleep not yet complete. Recovery will be available after wake."
}
```

**Response `502`:**
```json
{
  "status": "error",
  "reason": "Whoop API unavailable",
  "detail": "Connection timeout after 10s"
}
```

**Response `401`:**
```json
{
  "status": "error",
  "reason": "Reauthorization required"
}
```

---

### `GET /day/yesterday`

Полные данные за вчерашний день по MSK: нагрузка, сон со стадиями и respiratory rate.
Данные за прошлый день статичны — кэш, если есть, всегда валиден.

**Response `200`:**
```json
{
  "status": "ready",
  "date": "2026-02-26",
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
  "cached": false
}
```

**Response `502`** — аналогично `/recovery/today`.
**Response `401`** — `{"status":"error","reason":"Reauthorization required"}`.

---

### `GET /week`

Динамика за последние 7 дней (включая вчера, без сегодня).
Каждый день — отдельный объект. Порядок: от старого к новому.

Кэш пообъектный: каждый день — отдельный файл. Сервис запрашивает у Whoop только дни без кэша.
Если Whoop недоступен и хотя бы один день не закэширован — `502`.
Если за день данных нет в Whoop (не носил браслет) — день включается с `status: "missing"`.

**Response `200`:**
```json
{
  "period": {
    "from": "2026-02-20",
    "to": "2026-02-26"
  },
  "days": [
    {
      "date": "2026-02-20",
      "status": "ready",
      "recovery_score": 81,
      "recovery_zone": "green",
      "hrv_ms": 61,
      "resting_hr_bpm": 46,
      "strain_score": 11.3,
      "sleep_score": 85,
      "sleep_hours": 7.9
    },
    {
      "date": "2026-02-21",
      "status": "missing"
    }
  ]
}
```

**Response `502`** — аналогично `/recovery/today`.

---

### `GET /cycles`

История физиологических циклов за произвольный диапазон (MSK), с локальной пагинацией.

Query:
- `start` (required, ISO8601 datetime с timezone)
- `end` (optional, ISO8601 datetime с timezone, default = `now`)
- `limit` (optional, `1..25`, default `10`)
- `next_token` (optional, `YYYY-MM-DD`)
- глубина диапазона: `end - start <= 365 days` (иначе `422`)

**Response `200`:**
```json
{
  "status": "ready",
  "period": {
    "from": "2026-02-01",
    "to": "2026-03-02"
  },
  "days": [
    {
      "date": "2026-03-01",
      "cycle_id": 1339692348,
      "recovery_score": 70,
      "recovery_zone": "yellow",
      "hrv_ms": 101,
      "resting_hr_bpm": 49,
      "spo2_percentage": 96.5,
      "skin_temp_celsius": 33.6,
      "strain_score": 15.6,
      "kilojoules": 1823,
      "sleep_score": 72,
      "sleep_hours": 5.0,
      "sleep_disturbance_count": 3,
      "sleep_consistency_percentage": 89,
      "sleep_efficiency_percentage": 84
    }
  ],
  "next_token": "2026-03-02",
  "timezone_offset": "+03:00",
  "cached": false
}
```

Примечания:
- отсутствующие метрики из Whoop пропускаются (не возвращаются как `null`);
- `next_token` используется только в snake_case.
- если диапазон больше 14 дней, сервис выполняет weekly rollup (1 значение на неделю, усреднение числовых метрик).

---

### `GET /workouts`

Тренировки за диапазон дат с деталями спорта и зонами интенсивности.

Query:
- `start` (required, ISO8601 datetime с timezone)
- `end` (optional, ISO8601 datetime с timezone, default = `now`)
- `limit` (optional, `1..25`, default `10`)
- `next_token` (optional, passthrough токен Whoop)
- глубина диапазона: `end - start <= 365 days` (иначе `422`)

**Response `200`:**
```json
{
  "status": "ready",
  "period": {
    "from": "2026-02-01",
    "to": "2026-03-02"
  },
  "workouts": [
    {
      "workout_id": "w1",
      "date": "2026-03-01",
      "sport_name": "hockey",
      "start": "2026-03-01T15:00:00Z",
      "end": "2026-03-01T17:00:00Z",
      "strain_score": 12.4,
      "kilojoules": 1823,
      "average_hr_bpm": 112,
      "max_hr_bpm": 171,
      "distance_meter": 1772.77,
      "altitude_gain_meter": 46.64,
      "percent_recorded": 100,
      "zone_durations": {
        "zone_zero_milli": 300000,
        "zone_one_milli": 600000,
        "zone_two_milli": 900000,
        "zone_three_milli": 900000,
        "zone_four_milli": 600000,
        "zone_five_milli": 300000
      }
    }
  ],
  "next_token": null,
  "timezone_offset": "+03:00",
  "cached": false
}
```

Примечание: `sport_name` обязателен в ответе; если отсутствует у Whoop, сервис возвращает `"unknown"`.

---

### `GET /measurements/body`

Актуальный snapshot измерений тела из WHOOP `GET /v2/user/measurement/body`.

Query:
- нет

**Response `200` — `ready`:**
```json
{
  "status": "ready",
  "measured_at": "2026-03-02T12:10:59Z",
  "height_meter": 1.8288,
  "weight_kilogram": 90.7185,
  "max_heart_rate": 200,
  "timezone_offset": "+03:00",
  "cached": false
}
```

**Response `200` — `pending`:**
```json
{
  "status": "pending",
  "reason": "Body measurements are not available yet."
}
```

Примечания:
- отсутствующие поля WHOOP пропускаются (без `null`);
- при `ready` сервис сохраняет snapshot в локальную историю.

---

### `GET /measurements/body/history`

Synthetic history измерений тела из локально накопленных snapshots.

Query:
- `start` (required, ISO8601 datetime с timezone)
- `end` (optional, ISO8601 datetime с timezone, default = `now`)
- `limit` (optional, `1..25`, default `10`)
- `next_token` (optional, `YYYY-MM-DD`)
- глубина диапазона: `end - start <= 365 days` (иначе `422`)

**Response `200` — `ready`:**
```json
{
  "status": "ready",
  "period": {
    "from": "2026-02-01",
    "to": "2026-03-02"
  },
  "measurements": [
    {
      "date": "2026-03-02",
      "measured_at": "2026-03-02T12:10:59Z",
      "height_meter": 1.8288,
      "weight_kilogram": 90.7185,
      "max_heart_rate": 200
    }
  ],
  "next_token": null,
  "timezone_offset": "+03:00",
  "cached": true
}
```

**Response `200` — `pending`:**
```json
{
  "status": "pending",
  "reason": "Body measurements are not available yet."
}
```

Примечания:
- history не запрашивает upstream напрямую;
- `next_token` только snake_case;
- пагинация локальная по `date`.
- если диапазон больше 14 дней, history downsampled до weekly averages (примерно 8 точек за 2 месяца).

---

## Конфигурация (.env)

```
PROXY_API_KEY=            # статичный секрет для аутентификации агента
WHOOP_CLIENT_ID=          # из Whoop Developer Portal
WHOOP_CLIENT_SECRET=      # из Whoop Developer Portal
WHOOP_REDIRECT_URI=       # http://<VPS_IP>:<PORT>/auth/callback
TZ=Europe/Moscow
RANGE_READY_TTL_SECONDS=43200
RANGE_PENDING_TTL_SECONDS=300
```

---

## Docker

Сервис в одной Docker network с OpenClaw. Порт **не публикуется наружу**.
Базовый URL в скилле OpenClaw: `http://whoop-service:8001`.

Исключение: при первичной авторизации порт временно открывается для браузерного callback.

Volumes:
- `/cache` — файловый кэш (read/write, persist между перезапусками)
- `/secrets` — токены Whoop (**read/write** — сервис сам обновляет токены)

Restart policy: `unless-stopped`.

---

## Структура проекта

```
whoop-service/
├── Dockerfile
├── .env.example
├── app/
│   ├── main.py
│   ├── router.py          ← /recovery/today, /day/yesterday, /week, /cycles, /workouts, /measurements/body, /measurements/body/history
│   ├── auth_router.py     ← /auth/init, /auth/callback
│   ├── whoop_client.py    ← OAuth2 + запросы к Whoop API
│   ├── cache.py           ← файловый кэш + очистка по cron
│   ├── rate_limiter.py    ← защита от избыточных запросов
│   ├── models.py          ← Pydantic схемы ответов
│   └── config.py          ← настройки из .env
├── cache/
└── secrets/
    └── whoop_tokens.json  ← создаётся при первичной авторизации
```

---

## Что не входит в скоуп (Итерация 1)

- Кастомная логика зон (берём зоны напрямую из Whoop)
- Расчёт и хранение baseline HRV
- Интеграция с TrainHeroic
- Интеграция с Google Sheets
- HTTPS / TLS (трафик только внутри VPS)
