import logging
import threading
import time
import uuid

import httpx
from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from pydantic import BaseModel

from app.models.auth import SessionData
from app.services import riot_auth
from app.session_store import store

logger = logging.getLogger(__name__)

router = APIRouter()

# --- Rate limiting: 5 requests/minute per IP ---

_rate_lock = threading.Lock()
_rate_log: dict[str, list[float]] = {}
RATE_LIMIT = 5
RATE_WINDOW = 60.0


def _check_rate_limit(ip: str) -> None:
    now = time.monotonic()
    with _rate_lock:
        timestamps = _rate_log.get(ip, [])
        timestamps = [t for t in timestamps if now - t < RATE_WINDOW]
        if len(timestamps) >= RATE_LIMIT:
            raise HTTPException(status_code=429, detail="Too many requests. Try again later.")
        timestamps.append(now)
        _rate_log[ip] = timestamps


# --- Response models ---

class AuthStartResponse(BaseModel):
    auth_url: str
    auth_id: str


class AuthPollResponse(BaseModel):
    status: str  # "pending" | "success" | "error"
    puuid: str | None = None
    error: str | None = None


class CallbackResponse(BaseModel):
    status: str
    error: str | None = None


# --- Endpoints ---

@router.post("/start", response_model=AuthStartResponse)
async def start_login(request: Request) -> AuthStartResponse:
    """Start OAuth login flow. Returns the Riot auth URL and an auth_id for polling."""
    _check_rate_limit(request.client.host if request.client else "unknown")

    try:
        riot_auth.start_callback_server()
    except OSError as e:
        logger.error("Callback server failed: %s", e)
        raise HTTPException(status_code=503, detail="Auth service unavailable")

    auth_id = str(uuid.uuid4())
    auth_url = riot_auth.get_auth_url()

    return AuthStartResponse(auth_url=auth_url, auth_id=auth_id)


@router.post("/callback")
async def oauth_callback(request: Request, response: Response) -> CallbackResponse:
    """Receive tokens from the OAuth redirect page's JavaScript."""
    params = dict(request.query_params)

    access_token = params.get("access_token")
    id_token = params.get("id_token", "")

    if not access_token:
        return CallbackResponse(status="error", error="No access_token received")

    try:
        entitlements = await riot_auth.get_entitlements(access_token)
        puuid = await riot_auth.get_player_info(access_token)
        region, shard = await riot_auth.get_region(access_token, id_token)

        session_data = SessionData(
            access_token=access_token,
            entitlements_token=entitlements,
            puuid=puuid,
            shard=shard,
            region=region,
        )
        session_token = store.create(session_data)

        # Store the session token keyed by a temporary auth_id so the
        # frontend can pick it up via the /poll endpoint.
        auth_id = str(uuid.uuid4())
        riot_auth.store_pending_auth(auth_id, {
            "session_token": session_token,
            "puuid": puuid,
        })

        response.set_cookie(
            key="session_token",
            value=session_token,
            httponly=True,
            secure=False,  # localhost dev — set True in production
            samesite="lax",
            max_age=3 * 3600,
        )

        return CallbackResponse(status="success")
    except riot_auth.RateLimitError:
        return CallbackResponse(status="error", error="Rate limited by Riot servers. Try again shortly.")
    except riot_auth.AuthenticationError as e:
        logger.warning("Authentication failed: %s", e)
        return CallbackResponse(status="error", error=str(e))
    except httpx.HTTPStatusError as e:
        logger.error("Riot API HTTP error: %s", e.response.status_code)
        return CallbackResponse(status="error", error=f"Riot API error ({e.response.status_code})")
    except httpx.RequestError as e:
        logger.error("Network error contacting Riot: %s", e)
        return CallbackResponse(status="error", error="Could not reach Riot servers. Try again.")
    except Exception as e:
        logger.exception("OAuth callback failed")
        return CallbackResponse(status="error", error="Authentication failed unexpectedly")


@router.get("/poll")
async def poll_auth(
    response: Response,
    session_token: str | None = Cookie(default=None),
) -> AuthPollResponse:
    """Poll for auth completion. The session cookie is set by /callback."""
    if not session_token:
        return AuthPollResponse(status="pending")

    session = store.get(session_token)
    if not session:
        return AuthPollResponse(status="pending")

    return AuthPollResponse(status="success", puuid=session.puuid)


@router.post("/logout")
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
) -> dict:
    if session_token:
        store.delete(session_token)
        response.delete_cookie("session_token")
    return {"status": "ok"}


@router.get("/session")
async def check_session(
    session_token: str | None = Cookie(default=None),
) -> dict:
    if not session_token:
        return {"valid": False}

    session = store.get_or_reauth(session_token)
    if not session:
        return {"valid": False}

    return {"valid": True, "puuid": session.puuid}
