"""Public-client OAuth with PKCE, verified identity, and renewable sessions."""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import secrets
import threading
import time
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt

from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore

ISSUER = "https://auth.openai.com"
AUTHORIZE_URL = f"{ISSUER}/api/accounts/authorize"
TOKEN_URL = f"{ISSUER}/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
USAGE_URL = "https://chatgpt.com/settings/usage"
PLAN_SCOPE = "chatgpt.tokens.use.direct"
SCOPES = f"openid profile email offline_access resource.invoke {PLAN_SCOPE}"
DYNAMIC_CLIENT_ID = "dynamic_agent_client"


class ChatGPTAuthError(RuntimeError):
    """A credential or authorization failure safe to display to the user."""


class _LoopbackServer(HTTPServer):
    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


@dataclass
class SignInAttempt:
    provider_name: str
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(64), repr=False)
    client_id: str = DYNAMIC_CLIENT_ID
    redirect_uri: str = ""
    status: str = "pending"
    message: str = "Waiting for ChatGPT sign-in."
    expires_at: float = field(default_factory=lambda: time.time() + 300)
    server: HTTPServer | None = field(default=None, repr=False)
    authorization_url: str = field(default="", repr=False)


class ChatGPTAuthManager:
    """Own callback listeners; expose account information without credentials."""

    def __init__(self, store: ChatGPTCredentialStore | None = None) -> None:
        self.store = store or ChatGPTCredentialStore()
        self._attempts: dict[str, SignInAttempt] = {}
        self._lock = threading.RLock()
        self._jwks = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json", timeout=15)

    def session(self, provider_name: str) -> dict[str, Any]:
        profile = self.store.get(provider_name)
        connected = bool(profile.get("access_token"))
        return {
            "connected": connected,
            "sharing": connected and PLAN_SCOPE in profile.get("scopes", []),
            "email": profile.get("email", ""),
            "usage_url": USAGE_URL,
        }

    def start_sign_in(self, provider_name: str) -> dict[str, str]:
        with self._lock:
            for attempt_id, pending in list(self._attempts.items()):
                if pending.expires_at < time.time():
                    del self._attempts[attempt_id]
                elif pending.provider_name == provider_name and pending.status == "pending":
                    raise ChatGPTAuthError("A ChatGPT sign-in is already in progress. Wait for it to finish or expire.")
            profile = self.store.get(provider_name)
            host_id = self.store.host_id()
            attempt = SignInAttempt(provider_name=provider_name, client_id=profile.get("client_id", DYNAMIC_CLIENT_ID))
            manager = self

            class CallbackHandler(BaseHTTPRequestHandler):
                def log_message(self, *_args) -> None:
                    pass

                def do_GET(self) -> None:
                    parsed = urlsplit(self.path)
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    state = params.get("state", [""])
                    if parsed.path != "/auth/callback" or len(state) != 1 or not secrets.compare_digest(state[0], attempt.state):
                        self.send_error(400, "Invalid OAuth callback.")
                        return
                    if any(len(values) != 1 for values in params.values()) or attempt.status != "pending":
                        self.send_error(400, "Invalid OAuth callback.")
                        return
                    try:
                        manager.complete_sign_in(attempt, {key: values[0] for key, values in params.items()})
                        attempt.status = "completed"
                        attempt.message = "ChatGPT connected. Return to k41."
                    except Exception:
                        attempt.status = "failed"
                        attempt.message = "ChatGPT sign-in failed. Check plan permission and try signing in again."
                    body = attempt.message.encode("utf-8")
                    self.send_response(200 if attempt.status == "completed" else 400)
                    self.send_header("Content-Type", "text/plain; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            server = _LoopbackServer(("127.0.0.1", 0), CallbackHandler)
            server.timeout = 0.5
            attempt.server = server
            attempt.redirect_uri = f"http://127.0.0.1:{server.server_port}/auth/callback"
            attempt_id = secrets.token_urlsafe(32)
            self._attempts[attempt_id] = attempt
            challenge = base64.urlsafe_b64encode(hashlib.sha256(attempt.verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
            params = {
                "client_id": attempt.client_id, "response_type": "code",
                "redirect_uri": attempt.redirect_uri, "scope": SCOPES, "resource": RESOURCE,
                "state": attempt.state, "nonce": attempt.nonce,
                "code_challenge_method": "S256", "code_challenge": challenge,
                "ext_agent_host_id": host_id,
            }
            if attempt.client_id == DYNAMIC_CLIENT_ID:
                params["agent_name_hint"] = "k41"
            elif profile.get("id_token"):
                params["id_token_hint"] = profile["id_token"]
            elif profile.get("email"):
                params["login_hint"] = profile["email"]

            def listen() -> None:
                try:
                    while attempt.status == "pending" and time.time() < attempt.expires_at:
                        server.handle_request()
                    if attempt.status == "pending":
                        attempt.status = "failed"
                        attempt.message = "ChatGPT sign-in expired. Please try again."
                finally:
                    server.server_close()

            threading.Thread(target=listen, daemon=True, name="chatgpt-oauth").start()
            attempt.authorization_url = f"{AUTHORIZE_URL}?{urlencode(params)}"
            return {"attempt_id": attempt_id, "authorization_url": attempt.authorization_url}

    def authorization_url(self, provider_name: str, attempt_id: str) -> str:
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if (attempt is None or attempt.provider_name != provider_name
                    or attempt.status != "pending" or attempt.expires_at <= time.time()):
                raise ChatGPTAuthError("ChatGPT sign-in attempt not found or expired.")
            return attempt.authorization_url

    def attempt_status(self, provider_name: str, attempt_id: str) -> dict[str, Any]:
        with self._lock:
            attempt = self._attempts.get(attempt_id)
            if attempt is None or attempt.provider_name != provider_name:
                raise ChatGPTAuthError("ChatGPT sign-in attempt not found or expired.")
            return {"status": attempt.status, "message": attempt.message, **self.session(provider_name)}

    def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        try:
            response = httpx.post(TOKEN_URL, data={**data, "resource": RESOURCE}, timeout=20)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or not result.get("access_token") or str(result.get("token_type", "")).lower() != "bearer":
                raise ChatGPTAuthError("Invalid ChatGPT token response. Please sign in again.")
            return result
        except (httpx.HTTPError, ValueError):
            raise ChatGPTAuthError("ChatGPT authorization could not be renewed. Please sign in again.") from None

    def _verify_identity(self, token: str, client_id: str, nonce: str | None = None) -> dict[str, Any]:
        signing_key = self._jwks.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, signing_key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER,
                            options={"require": ["exp", "iat", "sub", "aud", "iss"]})
        if nonce is not None and not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise ChatGPTAuthError("Invalid ChatGPT sign-in nonce.")
        return claims

    @staticmethod
    def _merge_tokens(profile: dict[str, Any], tokens: dict[str, Any]) -> dict[str, Any]:
        return {**profile, **{key: tokens[key] for key in ("access_token", "refresh_token", "id_token") if key in tokens},
                "scopes": str(tokens["scope"]).split() if "scope" in tokens else profile.get("scopes", []),
                "expires_at": time.time() + float(tokens.get("expires_in", 3600))}

    def complete_sign_in(self, attempt: SignInAttempt, params: dict[str, str]) -> None:
        with self._lock:
            self._complete_sign_in(attempt, params)
            attempt.status = "completed"

    def _complete_sign_in(self, attempt: SignInAttempt, params: dict[str, str]) -> None:
        if attempt.status != "pending" or time.time() >= attempt.expires_at or not secrets.compare_digest(params.get("state", ""), attempt.state):
            raise ChatGPTAuthError("Invalid or expired ChatGPT authorization state.")
        if params.get("error") or not params.get("code"):
            raise ChatGPTAuthError("ChatGPT sign-in was not authorized.")
        issued_id = params.get("client_id") or attempt.client_id
        if not issued_id or issued_id == DYNAMIC_CLIENT_ID:
            raise ChatGPTAuthError("ChatGPT did not return an issued client ID.")
        if attempt.client_id != DYNAMIC_CLIENT_ID and issued_id != attempt.client_id:
            raise ChatGPTAuthError("ChatGPT registration changed during sign-in.")
        with self.store.transaction() as transaction:
            profile = self.store.read(transaction, attempt.provider_name)
            self.store.write(transaction, attempt.provider_name, {**profile, "client_id": issued_id})
        tokens = self._token_request({"grant_type": "authorization_code", "client_id": issued_id,
                                      "code": params["code"], "code_verifier": attempt.verifier,
                                      "redirect_uri": attempt.redirect_uri})
        claims = self._verify_identity(tokens.get("id_token", ""), issued_id, attempt.nonce)
        with self.store.transaction() as transaction:
            profile = self.store.read(transaction, attempt.provider_name)
            if profile.get("subject") and (profile["subject"] != claims["sub"] or profile.get("issuer") != claims["iss"]):
                raise ChatGPTAuthError("The ChatGPT account does not match this saved connection.")
            for key in ("access_token", "refresh_token", "id_token", "scopes", "expires_at"):
                profile.pop(key, None)
            profile = self._merge_tokens(profile, {**tokens, "scope": tokens.get("scope", "")})
            profile.update(subject=claims["sub"], issuer=claims["iss"], email=claims.get("email", ""))
            self.store.write(transaction, attempt.provider_name, profile)

    def access_token(self, provider_name: str) -> str:
        with self.store.transaction() as transaction:
            profile = self.store.read(transaction, provider_name)
            if not profile.get("access_token"):
                raise ChatGPTAuthError("Continue with ChatGPT in provider settings before using this provider.")
            if PLAN_SCOPE not in profile.get("scopes", []):
                raise ChatGPTAuthError(f"ChatGPT plan usage is not enabled. Manage access at {USAGE_URL} and sign in again.")
            if profile.get("expires_at", 0) <= time.time() + 60:
                if not profile.get("refresh_token"):
                    raise ChatGPTAuthError("ChatGPT session expired. Please sign in again.")
                tokens = self._token_request({"grant_type": "refresh_token", "client_id": profile["client_id"],
                                              "refresh_token": profile["refresh_token"]})
                if tokens.get("id_token"):
                    claims = self._verify_identity(tokens["id_token"], profile["client_id"])
                    if claims["sub"] != profile["subject"]:
                        raise ChatGPTAuthError("ChatGPT identity changed during renewal. Please sign in again.")
                profile = self._merge_tokens(profile, tokens)
                self.store.write(transaction, provider_name, profile)
        if PLAN_SCOPE not in profile.get("scopes", []):
            raise ChatGPTAuthError("ChatGPT plan usage permission was removed. Please sign in again.")
        return profile["access_token"]

    def close(self) -> None:
        with self._lock:
            for attempt in self._attempts.values():
                if attempt.status == "pending":
                    attempt.status = "failed"
                    attempt.message = "ChatGPT sign-in cancelled."

    def sign_out(self, provider_name: str) -> dict[str, Any]:
        with self._lock:
            for attempt in self._attempts.values():
                if attempt.provider_name == provider_name and attempt.status == "pending":
                    attempt.status = "failed"
                    attempt.message = "ChatGPT sign-in cancelled."
            confirmed = True
            with self.store.transaction() as transaction:
                profile = self.store.read(transaction, provider_name)
                if profile.get("refresh_token"):
                    try:
                        discovery = httpx.get(f"{ISSUER}/.well-known/openid-configuration", timeout=15)
                        discovery.raise_for_status()
                        endpoint = discovery.json()["revocation_endpoint"]
                        if urlsplit(endpoint).scheme != "https" or urlsplit(endpoint).netloc != "auth.openai.com":
                            raise ChatGPTAuthError("Invalid ChatGPT revocation endpoint.")
                        confirmed = False
                        for attempt_index in range(3):
                            try:
                                response = httpx.post(endpoint, data={"token": profile["refresh_token"],
                                    "token_type_hint": "refresh_token", "client_id": profile["client_id"]}, timeout=10)
                                confirmed = response.status_code == 200
                                if response.status_code < 500:
                                    break
                            except httpx.HTTPError:
                                pass
                            if attempt_index < 2:
                                time.sleep(0.2 * 2 ** attempt_index)
                    except (httpx.HTTPError, KeyError, ValueError, ChatGPTAuthError):
                        confirmed = False
                for key in ("access_token", "refresh_token", "id_token", "scopes", "expires_at"):
                    profile.pop(key, None)
                self.store.write(transaction, provider_name, profile)
            return {"status": "signed_out", "revocation_confirmed": confirmed,
                    "message": "Signed out of ChatGPT." if confirmed else
                    "Signed out locally. Remote revocation was not confirmed; disconnect k41 in ChatGPT Settings."}

    def remove_connection(self, provider_name: str) -> dict[str, Any]:
        with self._lock:
            result = self.sign_out(provider_name)
            with self.store.transaction() as (connection, _cipher):
                connection.execute("DELETE FROM credentials WHERE name = ?", (provider_name,))
            return result
