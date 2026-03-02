# Business Requirement: WHOOP Service API Expansion

**Версия**: 1.0  
**Статус**: Draft  
**Приоритет**: High  

---

## 1. Обзор

Расширить локальный WHOOP Service proxy для обеспечения Coach Ukai полной информацией о восстановлении, сне, нагрузке и контексте спорта. Текущий сервис предоставляет только базовые метрики; требуется доступ к детализированным данным для квалифицированного анализа тренировочного состояния атлета.

---

## 2. Проблема (As-Is)

- Coach Ukai видит только: Recovery%, HRV, RHR, Strain, Sleep Score, Sleep Hours
- **Отсутствуют**:
  - Какой спорт выполнен (хоккей vs волейбол vs зал)
  - Детали сна: циклы, помехи, эффективность, долг сна
  - Физиологические данные: SpO2, температура кожи
  - История дольше 7 дней
  - Распределение нагрузки по зонам интенсивности
- Результат: Coach работает вслепую, не может давать полноценные рекомендации без дополнительного контекста от атлета

---

## 3. Решение (To-Be)

Расширить WHOOP Service endpoints для обеспечения Coach Ukai полным набором данных из WHOOP API v2:
- История циклов (Cycles) за произвольный диапазон дат
- Детальные данные о тренировках (Workouts) с типом спорта
- Расширенные метрики восстановления и сна
- Физиологические маркеры

---

## 4. Функциональные требования

### FR-1: Endpoint GET `/cycles`
**Описание**: Возвращает список физиологических циклов (дней) за заданный диапазон дат.

**Параметры запроса**:
- `start` (required): datetime ISO 8601 (начало диапазона)
- `end` (optional): datetime ISO 8601 (конец диапазона; по умолчанию now)
- `limit` (optional): integer 1-25 (по умолчанию 10)
- `nextToken` (optional): string (pagination)

**Ответ (200 ready)**:
```json
{
  "status": "ready",
  "period": {
    "from": "2026-02-01",
    "to": "2026-03-02"
  },
  "days": [
    {
      "date": "2026-03-02",
      "cycle_id": 123456,
      "recovery_score": 86,
      "recovery_zone": "green",
      "hrv_ms": 110,
      "resting_hr_bpm": 48,
      "spo2_percentage": 96.5,
      "skin_temp_celsius": 33.8,
      "strain_score": 5.2,
      "kilojoules": 4809,
      "sleep_score": 74,
      "sleep_hours": 8.1,
      "sleep_disturbance_count": 3,
      "sleep_consistency_percentage": 92,
      "sleep_efficiency_percentage": 89
    }
  ],
  "next_token": null,
  "cached": false
}
```

**Отличие от текущего `/week`**: 
- Произвольный диапазон (не только последние 7 дней)
- Добавлены SpO2, skin_temp, sleep_disturbance_count, sleep_consistency, sleep_efficiency
- Pagination support

---

### FR-2: Endpoint GET `/workouts`
**Описание**: Возвращает все тренировки за заданный диапазон дат с деталями спорта и интенсивности.

**Параметры запроса**:
- `start` (required): datetime ISO 8601
- `end` (optional): datetime ISO 8601
- `limit` (optional): integer 1-25
- `nextToken` (optional): string

**Ответ (200 ready)**:
```json
{
  "status": "ready",
  "period": {
    "from": "2026-02-01",
    "to": "2026-03-02"
  },
  "workouts": [
    {
      "workout_id": "ecfc6a15-4661-442f-a9a4-f160dd7afae8",
      "date": "2026-03-01",
      "sport_name": "hockey",
      "start": "2026-03-01T18:00:00Z",
      "end": "2026-03-01T20:15:00Z",
      "strain_score": 4.5,
      "kilojoules": 4809,
      "average_hr_bpm": 57,
      "max_hr_bpm": 128,
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
  "cached": false
}
```

**Критично**: 
- `sport_name` обязателен для определения типа активности
- `zone_durations` показывает распределение по интенсивности (зоны 0-5)

---

### FR-3: Расширение `/recovery/today`
**Описание**: Добавить физиологические маркеры в текущий endpoint.

**Новые поля в ответе (200 ready)**:
```json
{
  "status": "ready",
  "date": "2026-03-02",
  "recovery_score": 86,
  "recovery_zone": "green",
  "hrv_ms": 110,
  "resting_hr_bpm": 48,
  "spo2_percentage": 96.5,         // NEW
  "skin_temp_celsius": 33.8,        // NEW
  "user_calibrating": false,        // NEW
  "cached": true
}
```

---

### FR-4: Расширение `/day/yesterday`
**Описание**: Добавить детали сна и физиологические маркеры.

**Новые поля в sleep объекте (200 ready)**:
```json
{
  "status": "ready",
  "date": "2026-03-01",
  "strain": { ... },
  "sleep": {
    "score": 74,
    "total_hours": 8.1,
    "performance_percent": 74,
    "respiratory_rate": 13.8,
    "stages": { ... },
    "disturbance_count": 3,              // NEW
    "sleep_cycle_count": 5,              // NEW
    "consistency_percentage": 92,        // NEW
    "efficiency_percentage": 89,         // NEW
    "sleep_needed_hours": 7.5,          // NEW (baseline)
    "sleep_debt_hours": 0.2,            // NEW
    "strain_related_need_hours": 0.5    // NEW
  },
  "cached": false
}
```

---

## 5. Технические требования

### TR-1: WHOOP API v2 Integration
- Использовать endpoints WHOOP API v2:
  - `GET /api/v2/cycles` (с параметрами start, end, limit, nextToken)
  - `GET /api/v2/workouts` (аналогично)
  - `GET /api/v2/recovery` (для enrichment текущих endpoints)
  - `GET /api/v2/sleep` (для детализации)
- Соблюдать OAuth scope: `read:cycles`, `read:workout`, `read:recovery`, `read:sleep`

### TR-2: Caching
- Кэшировать `ready` responses для циклов/workouts (TTL: 12 часов)
- `pending` responses не кэшировать или кэшировать с минимальным TTL (300s)
- Инвалидировать кэш при получении новых данных

### TR-3: Timezone Handling
- Сохранять текущее поведение: timezone по умолчанию Europe/Moscow
- Возвращать `timezone_offset` в каждом ответе для клиента

### TR-4: Error Handling
- 401: invalid/missing API key → попросить переавторизацию
- 502: upstream WHOOP issues → вернуть `{"status":"error","reason":"...","detail":"..."}`
- Graceful degradation: если поле отсутствует в WHOOP API, пропустить (не ломать response)

### TR-5: Pagination
- Реализовать `next_token` механизм для всех collection endpoints
- Поддерживать `limit` параметр (max 25, default 10)

---

## 6. Критерии приемки

- [ ] Endpoint `/cycles?start=X&end=Y` возвращает полный список циклов за диапазон с расширенными полями
- [ ] Endpoint `/workouts?start=X&end=Y` возвращает все тренировки с `sport_name` и `zone_durations`
- [ ] `/recovery/today` содержит `spo2_percentage`, `skin_temp_celsius`, `user_calibrating`
- [ ] `/day/yesterday` содержит расширенные sleep metrics: `disturbance_count`, `consistency_percentage`, `efficiency_percentage`, sleep_needed breakdown
- [ ] Все endpoints возвращают `cached` флаг
- [ ] Все endpoints поддерживают pagination (если результатов > limit)
- [ ] 401/502 ошибки обрабатываются согласно spec
- [ ] Тестирование: запрос истории за 30 дней возвращает корректные данные
- [ ] Тестирование: sport_name корректно маппится (hockey, volleyball, strength, и т.д.)

---

## 7. Контекст для Coach Ukai

После реализации Coach Ukai сможет:
1. Видеть **спорт** каждой тренировки → подстраивать рекомендации под контекст
2. Анализировать **историю за месяц** → выделять паттерны, не только недельные снимки
3. Мониторить **SpO2 и температуру** → ловить инфекции и перетренировку раньше
4. Понимать **качество сна** через disturbance и efficiency → давать рекомендации про сон
5. Видеть **долг сна и нужду от нагрузки** → персонализировать рекомендации

---

## 8. Зависимости

- WHOOP API v2 доступен и авторизирован
- Переменные окружения `WHOOP_SERVICE_BASE_URL`, `WHOOP_SERVICE_TOKEN` настроены правильно

---

## 9. Контактное лицо

Coach Ukai (тренировочный асистент)  
