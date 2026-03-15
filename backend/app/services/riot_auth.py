"""Riot authentication service.

Uses OAuth implicit-grant flow — the user logs in via their real browser on
Riot's login page. After login, Riot redirects to the backend's /redirect
endpoint where client-side JS captures the URL-fragment tokens and POSTs
them to /api/auth/callback.

Downstream API calls (entitlements, userinfo, geo, storefront) use the
access_token obtained from the redirect.
"""

import logging
import threading
from urllib.parse import quote

import httpx

logger = logging.getLogger(__name__)

ENTITLEMENTS_URL = "https://entitlements.auth.riotgames.com/api/token/v1"
USERINFO_URL = "https://auth.riotgames.com/userinfo"
GEO_URL = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"

# The redirect_uri is built dynamically from API_URL via get_auth_url().
_AUTH_URL_TEMPLATE = (
    "https://auth.riotgames.com/authorize"
    "?redirect_uri={redirect_uri}"
    "&client_id=riot-client"
    "&response_type=token%20id_token"
    "&nonce=1"
    "&scope=openid%20link%20ban%20lol_region%20account"
    "&prompt=login"
)

_api_url: str = "http://localhost:8000"

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


class RateLimitError(Exception):
    """Raised when Riot rate-limits the request."""


def _check_rate_limit(response: httpx.Response) -> None:
    if response.status_code == 429:
        raise RateLimitError("Rate limited by Riot auth servers")


# --- Pending auth store ---

_pending_auth: dict[str, dict] = {}
_pending_lock = threading.Lock()


def configure(api_url: str) -> None:
    """Store the public API URL used to build redirect URIs."""
    global _api_url
    _api_url = api_url.rstrip("/")
    logger.info("Riot auth configured with API_URL=%s", _api_url)


def get_redirect_page(auth_id: str) -> str:
    """Return the HTML page served at /redirect.

    The page extracts OAuth tokens from the URL fragment and POSTs them
    to /api/auth/callback along with the auth_id so the polling frontend
    can pick up the result.
    """
    backend_url = _api_url
    return f"""<!DOCTYPE html>
<html>
<head><title>Valorant Shop - Login</title></head>
<body style="background:#0F1923;color:#ECE8E1;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">
<div style="text-align:center" id="msg">
    <h2>Processing login...</h2>
    <script>
        const hash = window.location.hash.substring(1);
        if (hash) {{
            fetch('{backend_url}/api/auth/callback?auth_id={auth_id}&' + hash, {{
                method: 'POST',
                credentials: 'include',
            }}).then(r => r.json()).then(data => {{
                if (data.status === 'success') {{
                    document.getElementById('msg').innerHTML =
                        '<h2 style="color:#17E8B5">Login successful!</h2><p>You can close this tab and return to the app.</p>';
                }} else {{
                    document.getElementById('msg').innerHTML =
                        '<h2 style="color:#FF4655">Login failed</h2><p>' + (data.error || 'Unknown error') + '</p>';
                }}
            }}).catch(err => {{
                document.getElementById('msg').innerHTML =
                    '<h2 style="color:#FF4655">Error</h2><p>' + err.message + '</p>';
            }});
        }} else {{
            document.getElementById('msg').innerHTML =
                '<h2 style="color:#FF4655">No tokens received</h2>';
        }}
    </script>
</div>
</body></html>"""


def get_auth_url(auth_id: str) -> str:
    """Return the Riot OAuth URL, with redirect_uri pointing to our /redirect endpoint.

    The auth_id is passed as a query param on the redirect URI so the
    callback page can thread it through to /api/auth/callback.
    """
    redirect_uri = f"{_api_url}/redirect?auth_id={auth_id}"
    return _AUTH_URL_TEMPLATE.format(redirect_uri=quote(redirect_uri, safe=""))


def store_pending_auth(auth_id: str, tokens: dict) -> None:
    """Store tokens from a completed OAuth callback."""
    with _pending_lock:
        _pending_auth[auth_id] = tokens


def pop_pending_auth(auth_id: str) -> dict | None:
    """Pop and return pending auth tokens."""
    with _pending_lock:
        return _pending_auth.pop(auth_id, None)



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


async def cookie_reauth(cookies: dict[str, str]) -> dict | None:
    """Cookie reauth is not used in the OAuth flow."""
    return None
