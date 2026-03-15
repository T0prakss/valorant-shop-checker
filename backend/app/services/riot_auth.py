"""Riot authentication service.

Uses OAuth redirect flow — the user logs in via their real browser on Riot's
login page. After login, Riot redirects to localhost where we capture the tokens.

Downstream API calls (entitlements, userinfo, geo, storefront) use the
access_token obtained from the redirect.
"""

import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs

import httpx

logger = logging.getLogger(__name__)

ENTITLEMENTS_URL = "https://entitlements.auth.riotgames.com/api/token/v1"
USERINFO_URL = "https://auth.riotgames.com/userinfo"
GEO_URL = "https://riot-geo.pas.si.riotgames.com/pas/v1/product/valorant"

AUTH_URL = (
    "https://auth.riotgames.com/authorize"
    "?redirect_uri=http%3A%2F%2Flocalhost%2Fredirect"
    "&client_id=riot-client"
    "&response_type=token%20id_token"
    "&nonce=1"
    "&scope=openid%20link%20ban%20lol_region%20account"
    "&prompt=login"
)

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


# --- OAuth callback server (port 80) ---

_pending_auth: dict[str, dict] = {}
_pending_lock = threading.Lock()
_callback_server: HTTPServer | None = None
_callback_thread: threading.Thread | None = None


def _get_callback_page(backend_url: str) -> bytes:
    return f"""<!DOCTYPE html>
<html>
<head><title>Valorant Shop - Login</title></head>
<body style="background:#0F1923;color:#ECE8E1;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0">
<div style="text-align:center" id="msg">
    <h2>Processing login...</h2>
    <script>
        const hash = window.location.hash.substring(1);
        if (hash) {{
            fetch('{backend_url}/api/auth/callback?' + hash, {{
                method: 'POST',
                mode: 'cors',
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
</body></html>""".encode()


class _RedirectHandler(BaseHTTPRequestHandler):
    backend_url: str = "http://localhost:8000"

    def do_GET(self):
        if self.path.startswith("/redirect"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(_get_callback_page(self.backend_url))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_callback_server(backend_url: str = "http://localhost:8000") -> None:
    """Start the port-80 callback server if not already running."""
    global _callback_server, _callback_thread

    if _callback_server is not None:
        return

    _RedirectHandler.backend_url = backend_url

    try:
        _callback_server = HTTPServer(("localhost", 80), _RedirectHandler)
        _callback_thread = threading.Thread(target=_callback_server.serve_forever, daemon=True)
        _callback_thread.start()
        logger.info("Auth callback server started on port 80")
    except OSError as e:
        logger.error("Failed to start callback server on port 80: %s", e)
        logger.error("Try running with admin privileges, or free port 80")
        raise


def stop_callback_server() -> None:
    """Stop the callback server."""
    global _callback_server, _callback_thread
    if _callback_server:
        _callback_server.shutdown()
        _callback_server = None
        _callback_thread = None


def get_auth_url() -> str:
    """Return the Riot OAuth URL for the user to open in their browser."""
    return AUTH_URL


def store_pending_auth(auth_id: str, tokens: dict) -> None:
    """Store tokens from a completed OAuth callback."""
    with _pending_lock:
        _pending_auth[auth_id] = tokens


def pop_pending_auth(auth_id: str) -> dict | None:
    """Pop and return pending auth tokens."""
    with _pending_lock:
        return _pending_auth.pop(auth_id, None)


def get_pending_auth(auth_id: str) -> dict | None:
    """Check if pending auth is complete."""
    with _pending_lock:
        return _pending_auth.get(auth_id)


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
