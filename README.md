# Whoop Service

FastAPI proxy service between OpenClaw agent and WHOOP Developer API v2.

## Features

- API key auth (`X-API-Key`) for all data routes
- OAuth2 authorization code flow (`/auth/init`, `/auth/callback`)
- Token persistence in `/secrets/whoop_tokens.json` with auto refresh
- File cache in `/cache` (only `ready` responses)
- Daily cache cleanup on startup and at 03:00 MSK
- Local smoke tests, unit tests, and gated live integration tests

## Environment

Copy `.env.example` to `.env` and set values:

- `PROXY_API_KEY`
- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_REDIRECT_URI`
- `TZ=Europe/Moscow`
- `WHOOP_HTTP_LOG_ENABLED=true`
- `WHOOP_HTTP_LOG_LEVEL=INFO`
- `WHOOP_HTTP_LOG_BODY_MAX_CHARS=4000`
- `WHOOP_HTTP_LOG_REDACT_SENSITIVE=true`

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
