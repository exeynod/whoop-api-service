# Functional Solution: WHOOP Service API Expansion

**Основание**: `BR_WHOOP_SERVICE_EXPANSION.md`  
**Статус**: Proposed

## 1. Цель реализации

Расширить текущий WHOOP proxy-сервис так, чтобы Coach Ukai получал:
- историю физиологических циклов за произвольный диапазон;
- тренировки с типом спорта и зонами интенсивности;
- расширенные маркеры восстановления и сна;
- единый предсказуемый контракт с пагинацией, кэшем и обработкой ошибок.

## 2. Область изменений

### Новые endpoints

1. `GET /cycles`
2. `GET /workouts`

### Расширяемые endpoints

1. `GET /recovery/today`
2. `GET /day/yesterday`

## 3. Целевой API-контракт

### 3.1 `GET /cycles`

Параметры:
- `start` (required, ISO8601 datetime)
- `end` (optional, ISO8601 datetime, default = now)
- `limit` (optional, int, default 10, max 25)
- `next_token` (optional, string, `YYYY-MM-DD`)

Ответ:
- `status: "ready"`
- `period.from`, `period.to`
- `days[]` с расширенными полями (включая `spo2_percentage`, `skin_temp_celsius`, sleep metrics)
- `next_token`
- `cached`
- `timezone_offset`

### 3.2 `GET /workouts`

Параметры:
- `start` (required)
- `end` (optional)
- `limit` (optional, default 10, max 25)
- `next_token` (optional)

Ответ:
- `status: "ready"`
- `period.from`, `period.to`
- `workouts[]` с `sport_name`, `zone_durations`, HR-метриками
- `next_token`
- `cached`
- `timezone_offset`

### 3.3 `GET /recovery/today` (расширение)

Добавляем поля:
- `spo2_percentage`
- `skin_temp_celsius`
- `user_calibrating`
- `timezone_offset`

### 3.4 `GET /day/yesterday` (расширение)

Добавляем в `sleep`:
- `disturbance_count`
- `sleep_cycle_count`
- `consistency_percentage`
- `efficiency_percentage`
- `sleep_needed_hours`
- `sleep_debt_hours`
- `strain_related_need_hours`

Также добавляем в ответ:
- `timezone_offset`

## 4. Интеграция с WHOOP API v2

### 4.1 Источники данных

- `GET /v2/cycle`
- `GET /v2/workout`
- `GET /v2/recovery`
- `GET /v2/activity/sleep`

### 4.2 OAuth scope

Требуемый scope:
- `offline read:recovery read:sleep read:cycles read:workout`

Нужно обновить формирование URL авторизации в `WhoopClient.build_authorization_url`.

## 5. Маппинг данных

### 5.1 `/cycles`

Собираем день из комбинации `cycle + recovery + sleep` по целевой дате:
- Recovery: `recovery_score`, `recovery_zone`, `hrv_ms`, `resting_hr_bpm`, `spo2_percentage`, `skin_temp_celsius`
- Cycle: `strain_score`, `kilojoules`
- Sleep: `sleep_score`, `sleep_hours`, `disturbance_count`, `consistency_percentage`, `efficiency_percentage`

Если отдельные поля отсутствуют у WHOOP, пропускаем их в ответе (graceful degradation).

### 5.2 `/workouts`

Из workout record маппим:
- `workout_id`
- `sport_name`
- `start`, `end`, `date`
- `strain_score`, `kilojoules`
- `average_hr_bpm`, `max_hr_bpm`
- `distance_meter`, `altitude_gain_meter`
- `percent_recorded`
- `zone_durations.zone_zero_milli..zone_five_milli`

## 6. Валидация, пагинация, timezone

### 6.1 Валидация query

- `start` обязателен и парсится как timezone-aware datetime.
- `end >= start`.
- `limit` ограничивается диапазоном `1..25`.

### 6.2 Пагинация

- Для `/cycles` используем локальную пагинацию по агрегированным дням (`next_token=YYYY-MM-DD`).
- Для `/workouts` проксируем `next_token` в Whoop и возвращаем `next_token` из Whoop payload.

### 6.3 Timezone

- Дата и агрегация остаются в `Europe/Moscow` (или `TZ` из конфигурации).
- В каждый ответ добавляем `timezone_offset` в формате `+03:00`.

## 7. Кэширование

### 7.1 Политика TTL

- `/cycles`, `/workouts`:
  - `ready` кэшировать 12 часов;
  - `pending` TTL зарезервирован (`RANGE_PENDING_TTL_SECONDS=300`) для последующих итераций.
- `/recovery/today`, `/day/yesterday`, `/week`:
  - оставить текущую политику, но добавить новые поля в кэшируемый payload.

### 7.2 Ключи кэша

Для range endpoints включить в ключ:
- профиль;
- endpoint;
- `start`, `end`, `limit`, `next_token`.

Реализация через детерминированный hash от query-параметров.

### 7.3 Инвалидация

- Базовая: по TTL.
- Дополнительно: при свежем запросе и изменении состава записей (ID/updated_at) перезаписывать кэш.

## 8. Обработка ошибок

### 8.1 Ошибки клиента

- Отсутствует/невалидный `X-API-Key`: `401 Unauthorized`.
- Неверные query-параметры: `422` (стандарт FastAPI).

### 8.2 Ошибки upstream WHOOP

- `401` от WHOOP после refresh: вернуть `401` с `status=error`, `reason=Reauthorization required`.
- `5xx` или timeout от WHOOP: `502`, `status=error`, `reason`, `detail`.
- Неожиданная структура payload: `502`.

## 9. Изменения в коде (по файлам)

1. `app/models.py`
- Добавить схемы для `/cycles` и `/workouts`.
- Расширить `RecoveryReadyResponse` и `SleepResponse`.
- Добавить поле `timezone_offset` в целевые ответы.

2. `app/router.py`
- Добавить роуты `/cycles` и `/workouts`.
- Добавить query-параметры и валидацию.
- Обновить `_whoop_error_response` для возврата `401` при reauth.

3. `app/whoop_client.py`
- Добавить методы:
  - `fetch_cycles_range(...)`
  - `fetch_workouts_range(...)`
- Расширить маппинг:
  - `fetch_recovery(...)`
  - `fetch_yesterday_snapshot(...)`
- Добавить generic fetch для коллекций с пагинацией.
- Обновить OAuth scope (`read:workout`).

4. `app/cache.py`
- Добавить операции для range-кэша с TTL.
- Поддержать ключи, основанные на query-параметрах.

5. `app/config.py`
- Добавить настройки TTL для range endpoints: `RANGE_READY_TTL_SECONDS=43200`, `RANGE_PENDING_TTL_SECONDS=300`.

## 10. План тестирования

### 10.1 Unit

- `WhoopClient`:
  - маппинг новых полей recovery/sleep;
  - маппинг workouts + zone_durations;
  - пагинация `next_token`;
  - graceful degradation на отсутствующих полях.

### 10.2 Smoke/API

- Новые endpoint’ы `/cycles`, `/workouts`:
  - happy path;
  - cached true/false;
  - pagination path;
  - 401/502.
- Расширенные `/recovery/today`, `/day/yesterday`:
  - присутствие новых полей;
  - обратная совместимость по старым полям.

### 10.3 Integration (live, gated)

- Проверка доступности `GET /v2/workout`.
- История 30 дней на `/cycles`.
- Проверка `sport_name` на реальных данных.

## 11. Definition of Done

- Реализованы и протестированы `/cycles` и `/workouts`.
- `/recovery/today` и `/day/yesterday` возвращают все новые поля из BR.
- Пагинация и `limit` работают согласно контракту.
- Кэш range endpoint’ов соответствует TTL-политике.
- 401/502 сценарии соответствуют спецификации.
- Документация обновлена и согласована.
