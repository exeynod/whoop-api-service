# Manual Token Issuance (No Docker, No Service Callback)

Use this guide to generate WHOOP OAuth tokens manually and place them into the service secrets file.

## What you need

- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- Redirect URI registered in WHOOP Developer Dashboard
  - Example: `https://example.com/callback`

## 1) Get authorization code

Open in browser (replace placeholders):

```text
https://api.prod.whoop.com/oauth/oauth2/auth?response_type=code&client_id=<CLIENT_ID>&redirect_uri=<URL_ENCODED_REDIRECT_URI>&scope=offline%20read:recovery%20read:sleep%20read:cycles&state=<RANDOM_STATE>
```

After consent, browser is redirected to your `redirect_uri` with query parameter `code=...`.
Copy that `code`.

## 2) Exchange code for tokens

```bash
curl -sS -X POST "https://api.prod.whoop.com/oauth/oauth2/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "code=<PASTE_CODE_HERE>" \
  --data-urlencode "client_id=<CLIENT_ID>" \
  --data-urlencode "client_secret=<CLIENT_SECRET>" \
  --data-urlencode "redirect_uri=<REDIRECT_URI_EXACTLY_AS_REGISTERED>" \
  > /tmp/whoop_oauth.json
```

Check response:

```bash
cat /tmp/whoop_oauth.json
```

Expected fields: `access_token`, `refresh_token`, `expires_in`, optional `refresh_token_expires_in`.

## 3) Create service token file

Generate `/secrets/whoop_tokens.json` in repo (host path: `./secrets/whoop_tokens.json`):

```bash
python3 - <<'PY'
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

src = Path('/tmp/whoop_oauth.json')
raw = json.loads(src.read_text(encoding='utf-8'))

now = datetime.now(timezone.utc)
expires_in = int(raw.get('expires_in', 3600))
refresh_expires_in = raw.get('refresh_token_expires_in') or raw.get('refresh_expires_in')

payload = {
    'access_token': raw['access_token'],
    'refresh_token': raw['refresh_token'],
    'expires_at': (now + timedelta(seconds=expires_in)).isoformat(),
    'refresh_expires_at': (
        (now + timedelta(seconds=int(refresh_expires_in))).isoformat()
        if refresh_expires_in is not None
        else None
    ),
}

out = Path('secrets/whoop_tokens.json')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')
print(f'Wrote {out}')
PY
```

## 4) Start service (internal-only)

```bash
docker compose up -d --build
```

The service will rotate `access_token` automatically using `refresh_token`.
Manual re-login is required only if refresh token is expired/revoked.
