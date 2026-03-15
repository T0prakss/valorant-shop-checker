"""Riot authentication service.

Authenticates against Riot's auth API server-side using username/password
credentials. This avoids the OAuth browser redirect (which requires
localhost) and works for web-deployed apps.

Flow:
1. POST /api/v1/authorization — initialize auth session (gets cookies)
2. PUT  /api/v1/authorization — submit credentials
3. If MFA is required, PUT again with the MFA code
4. Extract tokens from the response's redirect URI

Downstream API calls (entitlements, userinfo, geo, storefront) use the
access_token obtained from the auth flow.
"""

import logging
import ssl
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

AUTH_BASE = "https://auth.riotgames.com/api/v1/authorization"
ENTITLEMENTS_URL = "https://entitlements.auth.riotgames.com/api/token/v1"
USERINFO_URL = "https://auth.riotgames.com/userinfo"
GEO_URL = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"

# Riot's auth API requires a Riot Client user-agent and specific TLS
# ciphers, otherwise it silently rejects credentials with auth_failure.
_RIOT_CLIENT_UA = (
    "RiotClient/60.0.6.4770705.4749685 rso-auth "
    "(Windows;10;;Professional, x64)"
)

_AUTH_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": _RIOT_CLIENT_UA,
    "Accept": "application/json",
}

# Build an SSL context with the ciphers Riot expects
RIOT_CIPHERS = (
    "ECDH+AESGCM:DH+AESGCM:ECDH+AES256:DH+AES256:"
    "ECDH+AES128:DH+AES:ECDH+HIGH:DH+HIGH:ECDH+3DES:"
    "DH+3DES:RSA+AESGCM:RSA+AES:RSA+HIGH:RSA+3DES:!aNULL:"
    "!eNULL:!MD5"
)


def _make_ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.set_ciphers(RIOT_CIPHERS)
    return ctx


_ssl_ctx = _make_ssl_ctx()

REGION_TO_SHARD = {
    "na": "na",
    "eu": "eu",
    "ap": "ap",
    "kr": "kr",
    "latam": "na",
    "br": "na",
}


class AuthenticationError(Exception):
    """Raised when authentication fails."""


class MfaRequiredError(Exception):
    """Raised when MFA is needed. Carries the email hint and cookies."""

    def __init__(self, email: str, cookies: dict[str, str]):
        super().__init__("Multifactor authentication required")
        self.email = email
        self.cookies = cookies


class RateLimitError(Exception):
    """Raised when Riot rate-limits the request."""


def _check_rate_limit(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise RateLimitError("Rate limited by Riot auth servers")


def _extract_tokens(uri: str) -> dict[str, str]:
    """Extract access_token and id_token from a redirect URI fragment."""
    fragment = urlparse(uri).fragment
    params = parse_qs(fragment)
    access_token = params.get("access_token", [None])[0]
    id_token = params.get("id_token", [""])[0]
    if not access_token:
        raise AuthenticationError("No access_token in auth response")
    return {"access_token": access_token, "id_token": id_token}


# --- Server-side credential auth ---

async def authenticate(username: str, password: str) -> dict[str, str]:
    """Authenticate with Riot using username/password.

    Returns dict with access_token and id_token.
    Raises MfaRequiredError if MFA is needed.
    """
    async with httpx.AsyncClient(
        timeout=30.0, headers=_AUTH_HEADERS, verify=_ssl_ctx
    ) as client:
        # Step 1: Initialize auth session (establishes cookies)
        init_resp = await client.post(
            AUTH_BASE,
            json={
                "client_id": "riot-client",
                "nonce": "1",
                "redirect_uri": "http://localhost/redirect",
                "response_type": "token id_token",
                "scope": "openid link ban lol_region account",
            },
        )
        _check_rate_limit(init_resp)
        init_resp.raise_for_status()

        # Step 2: Submit credentials (same client keeps cookies)
        auth_resp = await client.put(
            AUTH_BASE,
            json={
                "type": "auth",
                "username": username,
                "password": password,
                "remember": False,
                "language": "en_US",
            },
        )
        _check_rate_limit(auth_resp)
        auth_resp.raise_for_status()

        data = auth_resp.json()
        logger.debug("Auth response type=%s", data.get("type"))
        resp_type = data.get("type")

        if resp_type == "response":
            uri = data["response"]["parameters"]["uri"]
            return _extract_tokens(uri)

        if resp_type == "multifactor":
            email = data.get("multifactor", {}).get("email", "")
            # Collect all cookies from the session for MFA continuation
            cookies = dict(client.cookies)
            raise MfaRequiredError(email=email, cookies=cookies)

        if resp_type == "auth" and data.get("error") == "auth_failure":
            raise AuthenticationError("Invalid username or password")

        logger.warning("Unexpected auth response: %s", data)
        raise AuthenticationError(
            data.get("error", "Unknown authentication error")
        )


async def authenticate_mfa(code: str, cookies: dict[str, str]) -> dict[str, str]:
    """Complete MFA authentication with a code.

    Returns dict with access_token and id_token.
    """
    async with httpx.AsyncClient(
        timeout=30.0, headers=_AUTH_HEADERS, verify=_ssl_ctx, cookies=cookies
    ) as client:
        resp = await client.put(
            AUTH_BASE,
            json={
                "type": "multifactor",
                "code": code,
                "rememberDevice": False,
            },
        )
        _check_rate_limit(resp)
        resp.raise_for_status()

        data = resp.json()
        resp_type = data.get("type")

        if resp_type == "response":
            uri = data["response"]["parameters"]["uri"]
            return _extract_tokens(uri)

        if resp_type == "multifactor" and data.get("error") == "multifactor_attempt_failed":
            raise AuthenticationError("Invalid MFA code")

        raise AuthenticationError(
            data.get("error", "MFA verification failed")
        )


# --- Downstream API calls ---

async def get_entitlements(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            ENTITLEMENTS_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={},
        )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()["entitlements_token"]


async def get_player_info(access_token: str) -> str:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        _check_rate_limit(resp)
        resp.raise_for_status()
        return resp.json()["sub"]


async def get_region(access_token: str, id_token: str) -> tuple[str, str]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.put(
            GEO_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"id_token": id_token},
        )
        _check_rate_limit(resp)
        resp.raise_for_status()
        data = resp.json()
        region = data.get("affinities", {}).get("live", "na")
        shard = REGION_TO_SHARD.get(region, "na")
        return region, shard
