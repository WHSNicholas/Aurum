# -------------------------------------------------------------------------------------------------------------------- #
#                                                        Aurum                                                         #
#                                                     AI - ChatGPT                                                     #
# -------------------------------------------------------------------------------------------------------------------- #
"""
Connect Aurum to your ChatGPT subscription without an API key.

The browser handles sign-in, and Aurum keeps its own registration and credentials privately under data/ai/.
Later runs reuse that registration and renew expired access. Requests use the public Responses API and return
only completed output; unsuccessful requests are not retried automatically.

The setup and journal commands share this connection. Callers supply their own input and validate the result
before using it. Disconnect Aurum in ChatGPT Settings to revoke its permission to use your plan.
"""

# %% 0. Preamble ---------------------------------------------------------------------------------------------
# Imports
from base64 import urlsafe_b64encode
from contextlib import contextmanager
import fcntl
import hashlib
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import re
import secrets
import ssl
import tempfile
import time
from typing import Iterator
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4
import webbrowser

import jwt
import truststore
from openai import APIConnectionError, APIError, APITimeoutError, OpenAI
from openai.types.responses import Response, ResponseInputParam, ResponseTextConfigParam

# Connection settings
AUTH_URL = "https://auth.openai.com"
API_URL = "https://api.openai.com/v1"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
STATE_DIRECTORY = Path(__file__).resolve().parents[2] / "data" / "ai"
STATE_FILE = STATE_DIRECTORY / "chatgpt.json"
# Use macOS's trusted certificates even when this Python installation has no bundled certificate file.
TLS_CONTEXT = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


# %% 1. Connection -------------------------------------------------------------------------------------------
class ChatGPTError(RuntimeError):
    """
    Explain a failure with safe request diagnostics and any token usage already reported.

    Messages are written by Aurum, never copied from provider messages that might repeat private input.
    Optional metadata records where the failure occurred, its machine-readable cause and its request ID.
    """

    def __init__(
        self, message: str, *, stage: str | None = None, code: str | None = None,
        error_type: str | None = None, param: str | None = None, status_code: int | None = None,
        request_id: str | None = None, usage: dict | None = None,
    ) -> None:
        """
        Keep bounded diagnostic identifiers and token counts, omitting unsafe or missing metadata.
        """
        self.stage = _diagnostic_label(stage)
        self.code = _diagnostic_label(code)
        self.error_type = _diagnostic_label(error_type)
        self.param = _diagnostic_parameter(param)
        self.status_code = status_code if type(status_code) is int and 100 <= status_code <= 599 else None
        self.request_id = _diagnostic_request_id(request_id)
        self.usage = _token_usage(usage)
        details = [f"stage={self.stage}"] if self.stage else []
        if self.status_code is not None:
            details.append(f"HTTP {self.status_code}")
        for name, value in (
            ("code", self.code), ("type", self.error_type), ("param", self.param),
            ("request_id", self.request_id),
        ):
            if value:
                details.append(f"{name}={value}")
        super().__init__(message + (" [" + "; ".join(details) + "]" if details else ""))


class ChatGPTConnection:
    """
    Use one authenticated ChatGPT registration and its saved model selection.

    Construct connections through connect(), which protects saved credentials and serializes token renewal.
    Keep the connection inside that context so another process cannot refresh the same session concurrently.
    """

    def __init__(self, state: dict, path: Path) -> None:
        """
        Retain the private credential record and its storage path without displaying either.
        """
        self._state = state
        self._path = path

    @property
    def model(self) -> str | None:
        """
        Return the saved model identifier, or None until a model has been selected.
        """
        return self._state.get("model")

    def list_models(self) -> list[dict]:
        """
        Return the account's visible models, preserving the provider's ordering.

        Raises:
            ChatGPTError: The request fails or the model catalogue has an unexpected structure.
        """
        result = _request_json(
            f"{API_URL}/models", access_token=self._state["access_token"]
        )
        models = result.get("models")
        if not isinstance(models, list):
            raise ChatGPTError("ChatGPT returned an unexpected model catalogue.")
        return [
            {"slug": item["slug"], "display_name": item.get("display_name", item["slug"])}
            for item in models
            if isinstance(item, dict) and item.get("visibility") == "list"
            and isinstance(item.get("slug"), str) and item["slug"]
        ]

    def select_model(self, slug: str) -> None:
        """
        Remember a model identifier selected from list_models() by the calling command.
        """
        self._state["model"] = slug
        _save_state(self._path, self._state)

    def request(self, instructions: str, payload: dict, output_schema: dict) -> tuple[str, dict]:
        """
        Request one structured result, returning completed JSON text and token usage.

        The caller supplies instructions, input and the required JSON schema, then validates the
        returned text. No partial output is accepted. Requests are never retried or switched to API billing.

        Raises:
            ChatGPTError: No model is selected, access fails, or the response is refused or incomplete.
        """
        model = self.model
        if not model:
            raise ChatGPTError("Select a model before sending a request.")
        _require_permission(self._state)
        completed: Response | None = None
        request_id = None
        stream_status = None
        stream_open = False
        usage = {}
        text_parts = []
        refused = False
        messages: ResponseInputParam = [{"role": "user", "content": json.dumps(payload)}]
        text_format: ResponseTextConfigParam = {"format": {
            "type": "json_schema", "name": "aurum_response", "strict": True, "schema": output_schema,
        }}
        try:
            with OpenAI(
                api_key=self._state["access_token"], base_url=API_URL,
                organization="", project="", max_retries=0, timeout=90.0,
            ) as client:
                with client.responses.create(
                    model=model,
                    instructions=instructions,
                    input=messages,
                    text=text_format,
                    store=False,
                    stream=True,
                ) as stream:
                    stream_open = True
                    request_id = stream.response.headers.get("x-request-id")
                    stream_status = stream.response.status_code
                    for event in stream:
                        response = getattr(event, "response", None)
                        reported_usage = _token_usage(
                            getattr(response, "usage", None) or getattr(event, "usage", None)
                        )
                        if reported_usage:
                            usage = reported_usage
                        if event.type == "response.output_text.delta":
                            text_parts.append(event.delta)
                        if event.type in {"response.refusal.delta", "response.refusal.done"}:
                            refused = True
                        if event.type in {"response.failed", "response.incomplete", "error"}:
                            failure = getattr(response, "error", None) or event
                            incomplete = getattr(response, "incomplete_details", None)
                            code = getattr(failure, "code", None) or getattr(incomplete, "reason", None)
                            raise ChatGPTError(
                                "ChatGPT did not complete the response. This request was not retried.",
                                stage="stream", code=code, error_type=getattr(failure, "type", None),
                                param=getattr(failure, "param", None), status_code=stream_status,
                                request_id=request_id, usage=usage,
                            )
                        if event.type == "response.completed":
                            completed = event.response
        except APIError as error:
            status = getattr(error, "status_code", None)
            body = error.body if isinstance(error.body, dict) else {}
            failure = body.get("error") if isinstance(body.get("error"), dict) else body
            reported_usage = _token_usage(body.get("usage"))
            code = error.code or failure.get("code")
            if isinstance(error, APITimeoutError):
                code = "timeout"
            elif isinstance(error, APIConnectionError):
                code = "connection_error"
            raise ChatGPTError(
                "ChatGPT request failed. This request was not retried.",
                stage="stream" if stream_open else "request",
                code=code, error_type=error.type or failure.get("type"),
                param=error.param or failure.get("param"), status_code=status or stream_status,
                request_id=getattr(error, "request_id", None) or request_id,
                usage=reported_usage or usage,
            ) from None
        if completed is None or completed.status != "completed":
            raise ChatGPTError(
                "The response ended before completion. This request was not retried.",
                stage="stream", code="incomplete_stream", status_code=stream_status,
                request_id=request_id, usage=usage,
            )
        if refused:
            raise ChatGPTError(
                "ChatGPT declined the request. No classification was accepted.",
                stage="response", code="refusal", status_code=stream_status,
                request_id=request_id, usage=usage,
            )
        for item in completed.output:
            contents = getattr(item, "content", None) or []
            if item.type == "message" and any(part.type == "refusal" for part in contents):
                raise ChatGPTError(
                    "ChatGPT declined the request. No classification was accepted.",
                    stage="response", code="refusal", status_code=stream_status,
                    request_id=request_id, usage=usage,
                )
        # Some completed events omit text already delivered in deltas. Release it only after completion.
        output_text = completed.output_text or "".join(text_parts)
        if not output_text:
            raise ChatGPTError(
                "ChatGPT completed without a classification result.",
                stage="response", code="empty_output", status_code=stream_status,
                request_id=request_id, usage=usage,
            )
        return output_text, usage


@contextmanager
def connect(*, force_sign_in: bool = False) -> Iterator[ChatGPTConnection]:
    """
    Open a private ChatGPT connection, signing in or refreshing access when necessary.

    Set force_sign_in to repeat browser authorization with the saved registration. A local lock prevents
    overlapping runs from renewing the same rotating refresh token. No inference happens until request().

    Raises:
        ChatGPTError: Another setup is running, saved state is invalid, or authorization fails.
        OSError: Private state cannot be read or saved.
    """
    if STATE_DIRECTORY.is_symlink():
        raise ChatGPTError("The private AI directory must not be a symbolic link.")
    STATE_DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    STATE_DIRECTORY.chmod(0o700)
    lock_path = STATE_DIRECTORY / "chatgpt.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a+") as lock:
        os.fchmod(lock.fileno(), 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            message = "Another Aurum ChatGPT command is running. Close it before retrying."
            raise ChatGPTError(message) from None
        state = _load_state(STATE_FILE)
        if not state.get("ext_agent_host_id"):
            state["ext_agent_host_id"] = f"urn:uuid:{uuid4()}"
            _save_state(STATE_FILE, state)
        if force_sign_in or not state.get("access_token"):
            _sign_in(state, STATE_FILE)
        elif state.get("expires_at", 0) <= time.time() + 60:
            if state.get("refresh_token"):
                _refresh(state, STATE_FILE)
            else:
                _sign_in(state, STATE_FILE)
        _require_permission(state)
        yield ChatGPTConnection(state, STATE_FILE)


def record_usage(counts: dict[str, int], usage: dict) -> None:
    """
    Add valid provider-reported token counts to run totals, including usage from failed requests.
    """
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            counts[key] += value


# %% 2. Sign-In Helpers --------------------------------------------------------------------------------------
def _sign_in(state: dict, path: Path) -> None:
    """
    Authorize in the browser and save credentials only after validating the returned identity.
    """
    pending_state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    callback = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        """
        Receive the browser's loopback redirect without logging its authorization code.
        """

        # An idle browser connection must not block the overall sign-in timeout.
        timeout = 5

        def do_GET(self) -> None:
            """
            Accept only the expected callback path, unique parameters and matching OAuth state.
            """
            parts = urlsplit(self.path)
            values = parse_qs(parts.query, keep_blank_values=True)
            valid = (
                parts.path == "/auth/callback"
                and all(len(items) == 1 for items in values.values())
                and secrets.compare_digest(values.get("state", [""])[0].encode(), pending_state.encode())
            )
            if not valid:
                self.send_response(400)
                self.end_headers()
                return
            callback.update({key: items[0] for key, items in values.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(b"Return to Aurum. It will finish checking your sign-in.")

        def log_message(self, format: str, *args) -> None:
            """
            Suppress HTTP logs because callback URLs contain temporary authorization codes.
            """

    with HTTPServer(("127.0.0.1", 0), CallbackHandler) as server:
        server.timeout = 1
        redirect = f"http://127.0.0.1:{server.server_port}/auth/callback"
        client_id = state.get("client_id", "dynamic_agent_client")
        parameters = {
            "client_id": client_id, "ext_agent_host_id": state["ext_agent_host_id"],
            "response_type": "code", "redirect_uri": redirect, "scope": SCOPES,
            "resource": API_URL, "state": pending_state, "nonce": nonce,
            "code_challenge_method": "S256",
            "code_challenge": urlsafe_b64encode(
                hashlib.sha256(verifier.encode()).digest()
            ).decode().rstrip("="),
        }
        if client_id == "dynamic_agent_client":
            parameters["agent_name_hint"] = "Aurum"
        elif state.get("id_token"):
            parameters["id_token_hint"] = state["id_token"]
        if client_id != "dynamic_agent_client" and "chatgpt.tokens.use.direct" not in state.get("scopes", []):
            parameters["prompt"] = "consent"
        print("Continue with ChatGPT in your browser to authorise Aurum (five-minute timeout).")
        try:
            opened = webbrowser.open(f"{AUTH_URL}/api/accounts/authorize?{urlencode(parameters)}")
        except webbrowser.Error:
            opened = False
        if not opened:
            raise ChatGPTError("Could not open your browser. Check your default browser and run again.")
        deadline = time.monotonic() + 300
        while not callback and time.monotonic() < deadline:
            server.handle_request()
    if not callback:
        raise ChatGPTError("Browser sign-in timed out. Run the command again when ready.")
    if callback.get("error"):
        raise ChatGPTError("ChatGPT sign-in was declined or could not be completed.")
    issued_id = callback.get("client_id", client_id)
    if issued_id == "dynamic_agent_client" or not issued_id or not callback.get("code"):
        raise ChatGPTError("ChatGPT did not return a complete app registration.")
    if client_id != "dynamic_agent_client" and issued_id != client_id:
        raise ChatGPTError("The returned app registration did not match this sign-in.")
    # Retain the issued registration even if code exchange fails; the next attempt must reuse it.
    if client_id == "dynamic_agent_client":
        state["client_id"] = issued_id
        _save_state(path, state)
    tokens = _request_json(f"{AUTH_URL}/api/accounts/oauth/token", form={
        "grant_type": "authorization_code", "client_id": issued_id,
        "code": callback["code"], "code_verifier": verifier,
        "redirect_uri": redirect, "resource": API_URL,
    })
    _accept_tokens(state, tokens, path, nonce=nonce)


def _refresh(state: dict, path: Path) -> None:
    """
    Renew access once, replacing rotating credentials together while the connection lock is held.
    """
    tokens = _request_json(f"{AUTH_URL}/api/accounts/oauth/token", form={
        "grant_type": "refresh_token", "client_id": state["client_id"],
        "refresh_token": state["refresh_token"], "resource": API_URL,
    })
    _accept_tokens(state, tokens, path)


def _accept_tokens(state: dict, tokens: dict, path: Path, *, nonce: str | None = None) -> None:
    """
    Verify identity and token metadata before atomically replacing a saved credential record.
    """
    updated = state.copy()
    id_token = tokens.get("id_token")
    if id_token is not None and (not isinstance(id_token, str) or not id_token):
        raise ChatGPTError("ChatGPT returned an invalid identity token.")
    if nonce is not None and not id_token:
        raise ChatGPTError("Sign-in returned no identity token.")
    if id_token:
        try:
            keys = jwt.PyJWKClient(
                f"{AUTH_URL}/.well-known/jwks.json", timeout=20, ssl_context=TLS_CONTEXT
            )
            key = keys.get_signing_key_from_jwt(id_token)
            claims = jwt.decode(
                id_token, key.key, algorithms=["RS256"], audience=state["client_id"], issuer=AUTH_URL,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            if nonce is not None and not secrets.compare_digest(
                str(claims.get("nonce", "")).encode(), nonce.encode()
            ):
                raise ChatGPTError("The sign-in token did not match this authorization attempt.")
            if state.get("subject") and claims["sub"] != state["subject"]:
                raise ChatGPTError("Sign in with the ChatGPT account already registered for Aurum.")
            updated.update(subject=claims["sub"], id_token=id_token)
        except jwt.PyJWTError:
            raise ChatGPTError("The ChatGPT identity token could not be verified.") from None
    if not updated.get("subject"):
        raise ChatGPTError("The ChatGPT connection has no verified identity.")
    access_token = tokens.get("access_token")
    expires_in = tokens.get("expires_in")
    if (
        not isinstance(access_token, str) or not access_token
        or not isinstance(expires_in, (int, float)) or isinstance(expires_in, bool)
        or not math.isfinite(expires_in) or expires_in <= 0
        or str(tokens.get("token_type", "")).lower() != "bearer"
    ):
        raise ChatGPTError("ChatGPT returned incomplete access credentials.")
    scope = tokens.get("scope")
    if scope is not None and not isinstance(scope, str):
        raise ChatGPTError("ChatGPT returned invalid access permissions.")
    # Refresh responses may omit unchanged scopes; initial sign-in must supply its granted scopes.
    scopes = scope.split() if scope is not None else ([] if nonce else state.get("scopes", []))
    updated.update(access_token=access_token, expires_at=time.time() + expires_in, scopes=scopes)
    if "refresh_token" in tokens:
        if not isinstance(tokens["refresh_token"], str) or not tokens["refresh_token"]:
            raise ChatGPTError("ChatGPT returned an invalid renewal credential.")
        updated["refresh_token"] = tokens["refresh_token"]
    _save_state(path, updated)
    state.clear()
    state.update(updated)


def _require_permission(state: dict) -> None:
    """
    Stop before inference if the saved grant does not allow use of the ChatGPT plan.
    """
    if not {"chatgpt.tokens.use.direct", "resource.invoke"}.issubset(state.get("scopes", [])):
        raise ChatGPTError(
            "Aurum has not been granted ChatGPT plan access. Run with --sign-in and enable plan access."
        )


# %% 3. Storage And Request Helpers --------------------------------------------------------------------------
def _diagnostic_label(value: object) -> str | None:
    """
    Keep short machine labels, rejecting free text, credential-like strings and control characters.
    """
    if isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,99}", value):
        return value
    return None


def _diagnostic_parameter(value: object) -> str | None:
    """
    Keep a bounded API field path without accepting descriptions, URLs or arbitrary error text.
    """
    roots = {
        "input", "instructions", "model", "text", "response_format", "tools", "tool_choice", "reasoning",
        "store", "stream", "service_tier", "max_output_tokens",
    }
    if isinstance(value, str) and len(value) <= 128:
        if re.fullmatch(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*|\[[0-9]+])*", value):
            if re.split(r"[.\[]", value, maxsplit=1)[0] in roots:
                return value
    return None


def _diagnostic_request_id(value: object) -> str | None:
    """
    Keep OpenAI request identifiers without accepting tokens or arbitrary response-header content.
    """
    if isinstance(value, str) and len(value) <= 100:
        if re.fullmatch(r"req_[A-Za-z0-9_-]+|[a-fA-F0-9]{32}|[a-fA-F0-9-]{36}", value):
            return value
    return None


def _token_usage(value: object) -> dict:
    """
    Retain only known nonnegative token counters from a provider dictionary or SDK usage object.
    """
    result = {}
    for name in ("input_tokens", "output_tokens", "total_tokens"):
        count = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if type(count) is int and count >= 0:
            result[name] = count
    for name, counter in (
        ("input_tokens_details", "cached_tokens"), ("output_tokens_details", "reasoning_tokens"),
    ):
        details = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        count = details.get(counter) if isinstance(details, dict) else getattr(details, counter, None)
        if type(count) is int and count >= 0:
            result[name] = {counter: count}
    return result


def _load_state(path: Path) -> dict:
    """
    Read private connection state, rejecting malformed records without displaying their contents.
    """
    if not path.exists() and not path.is_symlink():
        return {}
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor) as handle:
            os.fchmod(handle.fileno(), 0o600)
            state = json.load(handle)
        if not isinstance(state, dict):
            raise ValueError
        if "expires_at" in state and (
            not isinstance(state["expires_at"], (int, float))
            or not math.isfinite(state["expires_at"])
        ):
            raise ValueError
        fields = (
            "client_id", "ext_agent_host_id", "subject", "id_token", "access_token", "refresh_token", "model"
        )
        for name in fields:
            if name in state and (not isinstance(state[name], str) or not state[name]):
                raise ValueError
        required = ("client_id", "subject", "expires_at")
        if state.get("access_token") and not all(state.get(key) for key in required):
            raise ValueError
        if "scopes" in state and (
            not isinstance(state["scopes"], list)
            or not all(isinstance(item, str) for item in state["scopes"])
        ):
            raise ValueError
        return state
    except (ValueError, TypeError):
        raise ChatGPTError("Saved ChatGPT connection details are invalid; no request was sent.") from None


def _save_state(path: Path, state: dict) -> None:
    """
    Replace the private JSON record atomically, with owner-only file permissions.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=".chatgpt-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as handle:
            json.dump(state, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _request_json(url: str, *, form: dict | None = None, access_token: str | None = None) -> dict:
    """
    Make one JSON HTTP request with optional form data, keeping error bodies out of console output.
    """
    headers = {"Accept": "application/json"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = Request(url, data=urlencode(form).encode() if form is not None else None, headers=headers)
    try:
        with urlopen(request, timeout=20, context=TLS_CONTEXT) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise ValueError
        return result
    except HTTPError as error:
        raise ChatGPTError(
            f"ChatGPT connection was rejected (HTTP {error.code}). Check plan access; "
            "run with --sign-in if your session has expired. No request was retried."
        ) from None
    except (URLError, TimeoutError):
        raise ChatGPTError("Could not reach ChatGPT. Check your connection and retry when ready.") from None
    except (ValueError, UnicodeError):
        raise ChatGPTError("ChatGPT returned an unreadable connection response.") from None
