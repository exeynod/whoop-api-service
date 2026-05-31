# Wiring the WHOOP service to a nanobot agent (Ukai)

How the WHOOP service is exposed to the Ukai nanobot bot, and the one
non-obvious gotcha that blocks the bot's `exec` tool from seeing the secrets.

## Topology

| Piece | Where |
|---|---|
| Service deploy | `infra/whoop-api-service/docker-compose.yml` (separate from the root nanobot compose). Container `whoop-service`, **internal-only** (no published ports), joins external network `nanobot-home_default`, `restart: unless-stopped`. Start: `docker compose up -d --build`. Mounts `./secrets:/secrets` + `./cache:/cache`, `env_file: .env`. |
| Bot reaches it at | `http://whoop-service:8001` (the Ukai container `nanobot-ukai` is on the same `nanobot-home_default` network). |
| Bot env vars | `.nanobot/secrets/ukai.env` → `WHOOP_SERVICE_BASE_URL=http://whoop-service:8001`, `WHOOP_SERVICE_TOKEN=<api_token>`. |
| Token must equal | an **active** profile's `api_token` in `secrets/whoop_tokens.json` (currently profile `denis`). Compare by sha256, never print. |
| Skill file | `.nanobot/instances/ukai/workspace/skills/whoop-api-service/SKILL.md` — copy the repo `SKILL.md` over on update; keep the `skillKey: whoopApiService` frontmatter stable. Restart `nanobot-ukai` to re-read a changed skill. |

## CRITICAL: how secrets reach the bot's `exec` tool

The nanobot `exec`/shell tool does **not** inherit the full process environment.
`nanobot/agent/tools/shell.py::_build_env` forwards only `HOME / LANG / TERM /
PYTHONUNBUFFERED` by default, plus any var names listed in the exec tool's
**`allowedEnvKeys`**. So even though `WHOOP_SERVICE_*` are in the nanobot
*process* env (via `env_file`), `${WHOOP_SERVICE_TOKEN}` inside a skill's `curl`
expands to empty unless the key is allow-listed.

Fix — in `.nanobot/instances/ukai/config.json`:

```json
"tools": {
  "exec": {
    "enabled": true,
    "sandbox": "",
    "allowedEnvKeys": ["WHOOP_SERVICE_BASE_URL", "WHOOP_SERVICE_TOKEN"]
  },
  "restrictToWorkspace": false
}
```

`allowedEnvKeys` is the camelCase alias of the pydantic field `allowed_env_keys`
(nanobot config uses `alias_generator=to_camel`, `populate_by_name=True`). After
editing, **`docker restart nanobot-ukai`** (config is read at startup).

> ⚠️ `NANOBOT_SHELL_ENV_ALLOWLIST` is **not** a real nanobot variable — nanobot
> never reads it. It was mistakenly in `ukai.env` and did nothing; removed.

nanobot source: `/home/exy/nanobot` (compose build context `../nanobot`); config
docs at `nanobot/docs/configuration.md`.

## "Bot has no access" — debug checklist

1. Is `whoop-service` actually running? `docker ps | grep whoop`. A down service
   shows as `Could not resolve host: whoop-service` from inside the bot.
2. Are `WHOOP_SERVICE_BASE_URL` / `WHOOP_SERVICE_TOKEN` in `tools.exec.allowedEnvKeys`
   in the bot's `config.json`? (The most common miss.)
3. Does `WHOOP_SERVICE_TOKEN` match an active profile's `api_token`?
4. Did `nanobot-ukai` get restarted after the config/env change?

Quick end-to-end check from the bot container:

```bash
docker exec nanobot-ukai sh -lc \
  'curl -sS "$WHOOP_SERVICE_BASE_URL/coach/status" -H "X-API-Key: $WHOOP_SERVICE_TOKEN"'
```
