from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse

from app.deps import get_whoop_client
from app.models import AuthCallbackResponse
from app.whoop_client import WhoopClient

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/init")
async def auth_init(
    profile: str = Query(default="default", min_length=1),
    whoop_client: WhoopClient = Depends(get_whoop_client),
) -> RedirectResponse:
    state = f"{profile}:{secrets.token_urlsafe(16)}"
    redirect_url = whoop_client.build_authorization_url(state=state)
    return RedirectResponse(url=redirect_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.get("/callback", response_model=AuthCallbackResponse)
async def auth_callback(
    code: str | None = Query(default=None),
    error: str | None = Query(default=None),
    state: str | None = Query(default=None),
    profile: str | None = Query(default=None),
    whoop_client: WhoopClient = Depends(get_whoop_client),
) -> AuthCallbackResponse:
    if error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Whoop OAuth error: {error}",
        )
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing OAuth code in callback.",
        )

    target_profile = profile or _parse_profile_from_state(state)
    if not target_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing profile context for callback.",
        )

    await whoop_client.exchange_code_for_tokens(profile_name=target_profile, code=code)
    return AuthCallbackResponse()


def _parse_profile_from_state(state: str | None) -> str | None:
    if not state:
        return None
    if ":" in state:
        return state.split(":", maxsplit=1)[0]
    return None
