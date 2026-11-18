"""
Signing AURORA in to your Google account (OAuth 2.0 for desktop apps, with PKCE).

    connect()      opens Google's sign-in in your browser; you choose the account and approve
                   exactly the permissions listed (read-only Gmail at first)
    access_token() a short-lived token for the Gmail API, renewed with the refresh token
    disconnect()   asks Google to revoke AURORA's access, then deletes the stored tokens

Safety:
  - your Google password is typed only into Google's page; AURORA never sees it
  - PKCE (a one-time secret this AURORA made) and a random "state" check: only this sign-in, started
    here, can finish here
  - Google's answer comes back to a one-time listener on 127.0.0.1 (this PC only), closed at once
  - tokens are stored encrypted (secrets.py); all requests to Google go through AURORA's internet gate

Your Google Cloud "Desktop app" OAuth client (its client id and the client secret Google calls
non-confidential for desktop apps) is read from settings.GMAIL_CLIENT_FILE, a file you download from
Google Cloud (kept outside the repository).
"""

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
READ_ONLY = "https://www.googleapis.com/auth/gmail.readonly"
SEND = "https://www.googleapis.com/auth/gmail.send"           # replies you approve (asked for separately)
SIGN_IN_SECONDS = 300


class AuthError(Exception):
    """Something went wrong signing in or renewing access (a message for you)."""


class SignInNeeded(AuthError):
    """AURORA isn't signed in, or Google ended the sign-in (expired or revoked): connect again."""


def load_client(path: Path) -> dict:
    """The OAuth client from the JSON file Google Cloud lets you download ("installed" app)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise AuthError("The Google sign-in isn't set up yet: the OAuth client file from Google Cloud is missing. "
                        "See Docs/GMAIL_SETUP.md.")
    except ValueError:
        raise AuthError("The OAuth client file from Google Cloud couldn't be read.")
    client = data.get("installed") or {}
    if not client.get("client_id") or not client.get("client_secret"):
        raise AuthError("That OAuth client file isn't a Google Cloud \"Desktop app\" client. See Docs/GMAIL_SETUP.md.")
    return {"client_id": client["client_id"], "client_secret": client["client_secret"]}


def challenge_for(verifier: str) -> str:
    """PKCE S256: base64url(sha256(verifier)) without padding (RFC 7636)."""
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(verifier, challenge) for PKCE with S256."""
    verifier = secrets.token_urlsafe(64)[:96]
    return verifier, challenge_for(verifier)


class _Catcher(BaseHTTPRequestHandler):
    """Takes Google's one redirect to http://127.0.0.1:PORT/ and shows a short page."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        query = {k: v[-1] for k, v in parse_qs(urlsplit(self.path).query).items()}
        if "code" not in query and "error" not in query:
            self.send_response(404)
            self.end_headers()
            return
        self.server.answer = query
        ok = "code" in query and query.get("state") == self.server.state
        body = ("<html><body style='font-family:sans-serif;background:#070b16;color:#e6edf7;padding:40px'>"
                f"<h2>{'AURORA is connected to Gmail.' if ok else 'The sign-in did not finish.'}</h2>"
                "<p>You can close this tab.</p></body></html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class GoogleAuth:
    """AURORA's sign-in to one Google account. [http] is an httpx client through the internet gate."""

    def __init__(self, client: dict, store, http: httpx.Client, clock=time.time, open_browser=webbrowser.open):
        self.client = client
        self.store = store
        self.http = http
        self.clock = clock
        self.open_browser = open_browser
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        tokens = self.store.load()
        return bool(tokens and tokens.get("refresh_token"))

    @property
    def scopes(self) -> list[str]:
        return (self.store.load() or {}).get("scopes", [])

    # ------------------------------------------------------------------ signing in

    def authorization_url(self, redirect: str, state: str, challenge: str, scopes: list[str], hint: str = "") -> str:
        params = {"client_id": self.client["client_id"], "redirect_uri": redirect, "response_type": "code",
                  "scope": " ".join(scopes), "state": state, "code_challenge": challenge,
                  "code_challenge_method": "S256", "access_type": "offline", "prompt": "consent",
                  "include_granted_scopes": "false"}
        if hint:
            params["login_hint"] = hint
        return AUTH_URL + "?" + urlencode(params)

    def connect(self, scopes: list[str] = (READ_ONLY,), timeout: float = SIGN_IN_SECONDS) -> list[str]:
        """Runs the sign-in in your browser; returns the permissions you granted. Blocking (up to [timeout])."""
        verifier, challenge = pkce_pair()
        state = secrets.token_urlsafe(24)
        server = HTTPServer(("127.0.0.1", 0), _Catcher)
        server.state, server.answer, server.timeout = state, None, 1
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        try:
            if not self.open_browser(self.authorization_url(redirect, state, challenge, list(scopes))):
                raise AuthError("Couldn't open the browser for the Google sign-in.")
            deadline = time.monotonic() + timeout            # real time, whatever clock tokens use
            while server.answer is None and time.monotonic() < deadline:
                server.handle_request()
        finally:
            server.server_close()
        answer = server.answer
        if answer is None:
            raise AuthError("The Google sign-in wasn't finished in time. Try again.")
        if answer.get("state") != state:
            raise AuthError("The sign-in answer didn't match this request, so it was ignored.")
        if "error" in answer:
            raise AuthError("Google sign-in was cancelled." if answer["error"] == "access_denied"
                            else f"Google refused the sign-in ({answer['error']}).")
        tokens = self._token({"grant_type": "authorization_code", "code": answer.get("code", ""),
                              "redirect_uri": redirect, "code_verifier": verifier})
        if not tokens.get("refresh_token"):
            raise AuthError("Google didn't give AURORA lasting access. Try connecting again.")
        granted = tokens.get("scope", "").split()
        self.store.save({"refresh_token": tokens["refresh_token"], "access_token": tokens.get("access_token", ""),
                         "expires_at": self.clock() + float(tokens.get("expires_in", 0)), "scopes": granted,
                         "connected_at": self.clock()})
        logger.info("Gmail: connected (%d permission(s))", len(granted))
        return granted

    # ------------------------------------------------------------------ using it

    def access_token(self) -> str:
        """A valid access token, renewed when it's about to expire. Raises SignInNeeded."""
        with self._lock:
            tokens = self.store.load()
            if not tokens or not tokens.get("refresh_token"):
                raise SignInNeeded("AURORA isn't connected to Gmail. Connect it in Settings → Gmail.")
            if tokens.get("access_token") and tokens.get("expires_at", 0) - 60 > self.clock():
                return tokens["access_token"]
            renewed = self._token({"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
            tokens.update(access_token=renewed["access_token"],
                          expires_at=self.clock() + float(renewed.get("expires_in", 3600)))
            if renewed.get("refresh_token"):
                tokens["refresh_token"] = renewed["refresh_token"]
            self.store.save(tokens)
            return tokens["access_token"]

    def expire_now(self) -> None:
        """The API said the token isn't valid any more: renew on next use."""
        with self._lock:
            tokens = self.store.load()
            if tokens:
                tokens["expires_at"] = 0
                self.store.save(tokens)

    def disconnect(self) -> str:
        """Asks Google to revoke AURORA's access and deletes the stored tokens. Returns what happened."""
        tokens = self.store.load() or {}
        self.store.delete()
        token = tokens.get("refresh_token")
        if not token:
            return "AURORA wasn't connected to Gmail."
        try:
            response = self.http.post(REVOKE_URL, data={"token": token}, timeout=20)
            revoked = response.status_code == 200
        except httpx.HTTPError:
            revoked = False
        logger.info("Gmail: disconnected (revoked at Google: %s)", revoked)
        return ("Disconnected: Google has revoked AURORA's access and the sign-in is deleted from this PC." if revoked
                else "Disconnected on this PC. Google couldn't be reached to revoke it; you can remove AURORA at "
                     "myaccount.google.com → Security → Third-party access.")

    def _token(self, form: dict) -> dict:
        form = {**form, "client_id": self.client["client_id"], "client_secret": self.client["client_secret"]}
        from aurora.network import NetworkBlocked
        try:
            response = self.http.post(TOKEN_URL, data=form, timeout=30)
        except NetworkBlocked as error:
            raise AuthError(str(error))
        except httpx.HTTPError as error:
            raise AuthError(f"Couldn't reach Google to sign in ({type(error).__name__}).")
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and data.get("access_token"):
            return data
        if data.get("error") == "invalid_grant":                 # expired (Testing mode: 7 days) or revoked
            self.store.delete()
            raise SignInNeeded("Google ended AURORA's Gmail sign-in (it expires after 7 days while your Google "
                               "Cloud app is in Testing, or it was revoked). Connect again in Settings → Gmail.")
        raise AuthError(f"Google refused the sign-in ({data.get('error', response.status_code)}).")
