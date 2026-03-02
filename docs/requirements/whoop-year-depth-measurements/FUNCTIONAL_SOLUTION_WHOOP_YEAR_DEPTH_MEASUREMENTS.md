# Functional Solution: WHOOP Year-Depth Queries and Body Measurements

**Основание**: `BR_WHOOP_YEAR_DEPTH_MEASUREMENTS.md`  
**Статус**: Proposed

## 1. Цель

Расширить Whoop Service следующими возможностями:
- диапазонные запросы `/cycles` и `/workouts` с ограничением глубины до 365 дней;
- получение актуальных телесных измерений через `GET /measurements/body`;
- получение истории измерений через `GET /measurements/body/history` из локально накопленных snapshots.
- для диапазонов больше 14 дней в time-series endpoints использовать weekly average rollup для снижения объёма ответа.

## 2. Область изменений

### Новые endpoints
- `GET /measurements/body`
- `GET /measurements/body/history`

### Расширяемые endpoints
- `GET /cycles`
- `GET /workouts`

## 3. Контракты API

### 3.1 Глубина диапазона (общая валидация)

Для endpoints:
- `/cycles`
- `/workouts`
- `/measurements/body/history`

действуют правила:
- `start` обязателен и timezone-aware;
- `end` optional, timezone-aware, default = `now`;
- `end >= start`;
- `end - start <= 365 days`, иначе `422` с детальным сообщением.

Rollup:
- `/cycles`: при диапазоне `>14` дней ответ downsampled до недельных усреднений;
- `/measurements/body/history`: при диапазоне `>14` дней ответ downsampled до недельных усреднений.

### 3.2 `GET /measurements/body`

Источник данных:
- `GET /v2/user/measurement/body`

Ответы:
- `200 ready`: snapshot с полями `measured_at`, `height_meter?`, `weight_kilogram?`, `max_heart_rate?`, `timezone_offset`, `cached=false`;
- `200 pending`: если upstream вернул `404` или в payload нет измерений;
- `401`: `Reauthorization required`;
- `502`: timeout/unavailable/unexpected payload.

Поведение:
- cache-read не используется;
- при `ready` snapshot сохраняется в локальный date-cache endpoint `body_measurement`.

### 3.3 `GET /measurements/body/history`

Источник данных:
- только локальные snapshots `body_measurement_YYYY-MM-DD.json`.

Query:
- `start`, `end`, `limit`, `next_token`.

Ответы:
- `200 ready`: `period`, `measurements[]`, `next_token`, `timezone_offset`, `cached=true`;
- `200 pending`: если snapshots за диапазон отсутствуют;
- `401/502`: стандартная семантика data-routes.

Пагинация:
- локальная по `date`;
- `next_token` только в `snake_case`, формат `YYYY-MM-DD`.
- при `>14` дней `measurements[]` содержит weekly aggregated точки.

## 4. OAuth и upstream

URL авторизации Whoop запрашивает scope:
- `offline read:recovery read:sleep read:cycles read:workout read:profile read:body_measurement`

Upstream path для measurements:
- strict `GET /v2/user/measurement/body` (без fallback).

## 5. Кэш и retention

### 5.1 Existing cache
- `/cycles` и `/workouts` продолжают использовать range-cache TTL (`RANGE_READY_TTL_SECONDS`).

### 5.2 New body snapshots
- Snapshot сохраняется как date-cache endpoint `body_measurement`.
- History endpoint читает эти snapshots.

### 5.3 Cleanup
- `body_measurement_*` хранится 365 дней;
- остальные date-cache endpoints — по `CACHE_RETENTION_DAYS`.

## 6. Изменения по файлам

1. `app/models.py`
- добавить модели snapshot/history для body measurements.

2. `app/router.py`
- добавить max-range валидацию (365 дней);
- применить к `/cycles`, `/workouts`, `/measurements/body/history`;
- добавить роуты `/measurements/body`, `/measurements/body/history`.
- добавить weekly rollup для body history при диапазоне `>14` дней.

3. `app/whoop_client.py`
- расширить scope в `build_authorization_url`;
- добавить `fetch_body_measurements(...)`;
- маппинг payload measurements и `404 -> pending`.
- добавить weekly rollup для `/cycles` при диапазоне `>14` дней.

4. `app/cache.py`
- добавить `save_body_snapshot(...)`;
- добавить `load_body_history(...)`;
- добавить endpoint-specific retention для cleanup (`body_measurement=365`).

5. `tests/unit/test_whoop_client.py`
- покрытие scope + body measurements flow.

6. `tests/smoke/test_http_smoke.py`
- новые smoke-сценарии по `/measurements/body` и `/measurements/body/history`;
- валидация диапазона `>365` для `/cycles`, `/workouts`, `/measurements/body/history`.

7. `tests/unit/test_cache.py`
- тесты body snapshots/history и retention.

8. `tests/integration/test_live_whoop_v2.py`
- добавить контрактный тест `GET /v2/user/measurement/body`.

9. Документация
- обновить `README.md`, `reqs.md`, `SKILL.md`.

## 7. Тестирование

Минимальный прогон:
- `pytest -m "unit or smoke" -q`

Gated прогон:
- `pytest -m integration -q` (при валидных live secrets).

## 8. Definition of Done

- Реализованы `/measurements/body` и `/measurements/body/history`.
- Добавлена валидация глубины диапазона `<=365 days` для целевых range endpoints.
- Snapshot measurements сохраняется и читается из synthetic history.
- Ошибки `401 Reauthorization required` и `502` соответствуют текущей семантике.
- Unit + smoke тесты проходят.
