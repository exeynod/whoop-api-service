# Multi-User Technical Design (Implemented)

## Scope

One service instance supports multiple users by profile.
Backward compatibility is not required.

## Mandatory Secrets Format

`/secrets/whoop_tokens.json` must have this shape:

```json
{
  "version": 2,
  "profiles": {
    "denis": {
      "api_token": "secret-for-x-api-key",
      "whoop": {
        "access_token": "...",
        "refresh_token": "...",
        "expires_at": "2026-03-02T10:00:00+00:00",
        "refresh_expires_at": null
      },
      "meta": {
        "active": true,
        "whoop_user_id": 12345,
        "created_at": "2026-03-02T09:15:00+00:00",
        "updated_at": "2026-03-02T09:15:00+00:00"
      }
    }
  }
}
```

## Authentication Model

1. Client sends `X-API-Key`.
2. Service finds matching `profiles.<name>.api_token` among active profiles.
3. If not found: `401 Unauthorized`.
4. If found: request is bound to `profile_name`.

No global API key from `.env` is used.

## Profile Isolation

### Whoop tokens

- Refresh and save are done only for resolved profile.
- Update is atomic (`*.tmp` then replace).

### Cache

- Cache path is profile-scoped: `/cache/<profile>/<endpoint>_YYYY-MM-DD.json`.
- Ready responses are isolated between profiles.

### Rate limiter

- Endpoint key includes profile (`<profile>:<endpoint>`).
- Pending replay behavior is isolated per profile.

## Endpoint Behavior

- Public: `/health`, `/auth/init`, `/auth/callback`.
- Protected by profile API token: `/recovery/today`, `/day/yesterday`, `/week`.
- OAuth callback stores tokens in selected profile (`profile` query or `state` prefix).

## Health Contract

`tokens_valid=true` means at least one active profile has non-expired refresh capability.

## Operations

For each user/profile:

1. Issue Whoop OAuth tokens.
2. Generate unique service API token.
3. Save both under one profile block in `whoop_tokens.json`.
4. User calls service with `X-API-Key: <their profile api_token>`.

## Security Notes

- Keep `/secrets/whoop_tokens.json` writable only by service UID/GID.
- Do not expose `whoop_tokens.json` via volume mounts to untrusted containers.
- Keep log redaction enabled to hide OAuth/API secrets in logs.
