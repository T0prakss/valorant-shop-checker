import logging
import threading
import time

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


# --- In-memory MFA session store ---

_mfa_sessions: dict[str, dict[str, str]] = {}
_mfa_lock = threading.Lock()


def _store_mfa_session(ip: str, cookies: dict[str, str]) -> None:
    with _mfa_lock:
        _mfa_sessions[ip] = cookies


def _pop_mfa_session(ip: str) -> dict[str, str] | None:
    with _mfa_lock:
        return _mfa_sessions.pop(ip, None)


# --- Request/response models ---

class LoginRequest(BaseModel):
    username: str
    password: str


class MfaRequest(BaseModel):
    code: str


class LoginResponse(BaseModel):
    status: str  # "success" | "mfa_required" | "error"
    puuid: str | None = None
    mfa_email: str | None = None
    error: str | None = None


# --- Endpoints ---

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, response: Response) -> LoginResponse:
    """Authenticate with Riot using username/password."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    try:
        tokens = await riot_auth.authenticate(body.username, body.password)
        return await _create_session(tokens, response)

    except riot_auth.MfaRequiredError as e:
        _store_mfa_session(client_ip, e.cookies)
        return LoginResponse(status="mfa_required", mfa_email=e.email)

    except riot_auth.RateLimitError:
        return LoginResponse(status="error", error="Rate limited by Riot servers. Try again shortly.")
    except riot_auth.AuthenticationError as e:
        return LoginResponse(status="error", error=str(e))
    except httpx.HTTPStatusError as e:
        logger.error("Riot API HTTP error: %s", e.response.status_code)
        return LoginResponse(status="error", error=f"Riot API error ({e.response.status_code})")
    except httpx.RequestError as e:
        logger.error("Network error contacting Riot: %s", e)
        return LoginResponse(status="error", error="Could not reach Riot servers. Try again.")
    except Exception:
        logger.exception("Login failed")
        return LoginResponse(status="error", error="Authentication failed unexpectedly")


@router.post("/mfa", response_model=LoginResponse)
async def submit_mfa(body: MfaRequest, request: Request, response: Response) -> LoginResponse:
    """Submit MFA code to complete authentication."""
    client_ip = request.client.host if request.client else "unknown"
    _check_rate_limit(client_ip)

    cookies = _pop_mfa_session(client_ip)
    if not cookies:
        return LoginResponse(status="error", error="MFA session expired. Please log in again.")

    try:
        tokens = await riot_auth.authenticate_mfa(body.code, cookies)
        return await _create_session(tokens, response)

    except riot_auth.RateLimitError:
        return LoginResponse(status="error", error="Rate limited by Riot servers. Try again shortly.")
    except riot_auth.AuthenticationError as e:
        # Put cookies back so user can retry with correct code
        _store_mfa_session(client_ip, cookies)
        return LoginResponse(status="error", error=str(e))
    except httpx.HTTPStatusError as e:
        logger.error("Riot API HTTP error: %s", e.response.status_code)
        return LoginResponse(status="error", error=f"Riot API error ({e.response.status_code})")
    except httpx.RequestError as e:
        logger.error("Network error contacting Riot: %s", e)
        return LoginResponse(status="error", error="Could not reach Riot servers. Try again.")
    except Exception:
        logger.exception("MFA verification failed")
        return LoginResponse(status="error", error="Authentication failed unexpectedly")


async def _create_session(tokens: dict[str, str], response: Response) -> LoginResponse:
    """Exchange Riot tokens for a local session."""
    access_token = tokens["access_token"]
    id_token = tokens.get("id_token", "")

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

    response.set_cookie(
        key="session_token",
        value=session_token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=3 * 3600,
    )

    return LoginResponse(status="success", puuid=puuid)


@router.post("/logout")
async def logout(
    response: Response,
    session_token: str | None = Cookie(default=None),
) -> dict:
    if session_token:
        store.delete(session_token)
        response.delete_cookie("session_token", secure=True, samesite="none")
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
