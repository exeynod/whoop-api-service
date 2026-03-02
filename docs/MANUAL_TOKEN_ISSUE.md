# Manual Token Issuance (Without Service Callback)

This guide creates or updates a profile in `/secrets/whoop_tokens.json` manually.
Backward compatibility is not supported: file format must be `version=2` with `profiles`.

## What you need

- `WHOOP_CLIENT_ID`
- `WHOOP_CLIENT_SECRET`
- `WHOOP_REDIRECT_URI` (must exactly match Whoop app settings)
- Profile name (example: `denis`)

## 1) Get OAuth `code`

Open in browser:

```text
https://api.prod.whoop.com/oauth/oauth2/auth?response_type=code&client_id=<CLIENT_ID>&redirect_uri=<URL_ENCODED_REDIRECT_URI>&scope=offline%20read:recovery%20read:sleep%20read:cycles&state=<PROFILE_NAME>:<RANDOM>
```

After consent, copy `code` from redirect URL.

## 2) Exchange `code` to Whoop tokens

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

## 3) Generate service API token for profile

```bash
openssl rand -hex 32
```

Save this value. It is sent in `X-API-Key` for this profile.

## 4) Write/update `/secrets/whoop_tokens.json`

This command merges profile data into existing file (or creates new one):

```bash
export PROFILE_NAME="denis"
export PROFILE_API_TOKEN="<PASTE_GENERATED_API_TOKEN>"
export PROFILE_WHOOP_USER_ID=""  # optional; leave empty if unknown

python3 - <<'PY'
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

profile_name = os.environ["PROFILE_NAME"]
api_token = os.environ["PROFILE_API_TOKEN"]
whoop_user_id_raw = os.environ.get("PROFILE_WHOOP_USER_ID", "").strip()
whoop_user_id = int(whoop_user_id_raw) if whoop_user_id_raw else None

oauth = json.loads(Path("/tmp/whoop_oauth.json").read_text(encoding="utf-8"))
now = datetime.now(timezone.utc)
expires_in = int(oauth.get("expires_in", 3600))
refresh_expires_in = oauth.get("refresh_token_expires_in") or oauth.get("refresh_expires_in")
refresh_expires_at = (
    (now + timedelta(seconds=int(refresh_expires_in))).isoformat()
    if refresh_expires_in is not None
    else None
)

dst = Path("secrets/whoop_tokens.json")
dst.parent.mkdir(parents=True, exist_ok=True)
if dst.exists():
    payload = json.loads(dst.read_text(encoding="utf-8"))
else:
    payload = {"version": 2, "profiles": {}}

payload.setdefault("version", 2)
payload.setdefault("profiles", {})

existing = payload["profiles"].get(profile_name, {})
meta = existing.get("meta", {})
created_at = meta.get("created_at") or now.isoformat()

payload["profiles"][profile_name] = {
    "api_token": api_token,
    "whoop": {
        "access_token": oauth["access_token"],
        "refresh_token": oauth["refresh_token"],
        "expires_at": (now + timedelta(seconds=expires_in)).isoformat(),
        "refresh_expires_at": refresh_expires_at,
    },
    "meta": {
        "active": True,
        "whoop_user_id": whoop_user_id,
        "created_at": created_at,
        "updated_at": now.isoformat(),
    },
}

dst.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
print(f"Updated {dst} profile={profile_name}")
PY
```

## File schema

```json
{
  "version": 2,
  "profiles": {
    "denis": {
      "api_token": "random-secret-for-x-api-key",
      "whoop": {
        "access_token": "...",
        "refresh_token": "...",
        "expires_at": "2026-03-02T12:00:00+00:00",
        "refresh_expires_at": null
      },
      "meta": {
        "active": true,
        "whoop_user_id": 12345,
        "created_at": "2026-03-02T11:00:00+00:00",
        "updated_at": "2026-03-02T11:00:00+00:00"
      }
    }
  }
}
```

## 5) Runtime behavior

- Service auto-refreshes `access_token` using `refresh_token`.
- On successful refresh, profile block in `whoop_tokens.json` is updated atomically.
- Manual OAuth login is needed only when refresh token is expired/revoked.
