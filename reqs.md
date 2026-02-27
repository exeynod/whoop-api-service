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
Если `refresh_token` протух — все эндпоинты данных возвращают `502` с `reason: "Reauthorization required"`.
Требуется ручное переподключение через `/auth/init`.

Credentials (`client_id`, `client_secret`) — из `.env`.

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

### Очистка кэша
Файлы старше 30 дней удаляются по двум триггерам:
- Cron внутри контейнера: раз в сутки в 03:00 MSK
- При старте сервиса (дополнительно)

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
| Refresh token протух | `502` | `error` | `reason: "Reauthorization required"` |
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
    "stages": {
      "deep_hours": 1.6,
      "rem_hours": 1.9,
      "light_hours": 3.2,
      "awake_hours": 0.7
    }
  },
  "cached": false
}
```

**Response `502`** — аналогично `/recovery/today`.

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

## Конфигурация (.env)

```
PROXY_API_KEY=            # статичный секрет для аутентификации агента
WHOOP_CLIENT_ID=          # из Whoop Developer Portal
WHOOP_CLIENT_SECRET=      # из Whoop Developer Portal
WHOOP_REDIRECT_URI=       # http://<VPS_IP>:<PORT>/auth/callback
TZ=Europe/Moscow
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
│   ├── router.py          ← /recovery/today, /day/yesterday, /week
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