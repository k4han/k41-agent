"""Verify ChatGPT OAuth and inference contracts without live credentials."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from types import SimpleNamespace
import time
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
import jwt
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage, SystemMessage, ToolMessage
from openai import AsyncOpenAI, OpenAI
import pytest

from agent.modules.providers.chatgpt.auth import (
    ChatGPTAuthError, ChatGPTAuthManager, DYNAMIC_CLIENT_ID, ISSUER, PLAN_SCOPE,
    RESOURCE, SignInAttempt,
)
from agent.modules.providers.chatgpt.chat_model import ChatGPTChatModel
from agent.modules.providers.chatgpt.factory import ChatGPTFactory
from agent.modules.providers.chatgpt.storage import ChatGPTCredentialStore
from agent.modules.providers.models import ModelConfig
from agent.modules.providers.provider import ProviderConfig, ProviderType
from agent.modules.providers.repository import _build_provider_config


@pytest.fixture
def auth(tmp_path):
    manager = ChatGPTAuthManager(ChatGPTCredentialStore(tmp_path / "chatgpt"))
    yield manager
    manager.close()


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def save_profile(auth, name="chatgpt", **overrides):
    profile = {"client_id": "oaiapp_test", "subject": "account-1", "issuer": ISSUER,
               "email": "user@example.com", "access_token": "private-access",
               "refresh_token": "private-refresh", "id_token": "private-id",
               "scopes": [PLAN_SCOPE], "expires_at": time.time() + 3600, **overrides}
    with auth.store.transaction() as transaction:
        auth.store.write(transaction, name, profile)
    return profile


def configure_tokens(monkeypatch, auth, signing_key, attempt, **claims):
    claims = {"iss": ISSUER, "sub": "account-1", "aud": "oaiapp_test", "exp": time.time() + 600,
              "iat": time.time(), "nonce": attempt.nonce, "email": "user@example.com", **claims}
    token = jwt.encode(claims, signing_key, algorithm="RS256")
    monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", lambda _token: SimpleNamespace(key=signing_key.public_key()))
    exchanges = []

    def exchange(data):
        exchanges.append(data)
        return {"access_token": "new-access", "refresh_token": "new-refresh", "id_token": token,
                "token_type": "Bearer", "scope": f"openid {PLAN_SCOPE}", "expires_in": 3600}

    monkeypatch.setattr(auth, "_token_request", exchange)
    return exchanges


def callback(attempt, **params):
    return {"state": attempt.state, "code": "auth-code", "client_id": "oaiapp_test", **params}


def test_store_encryption_host_and_session_redaction(auth):
    save_profile(auth)
    assert auth.store.host_id() == ChatGPTCredentialStore(auth.store.directory).host_id()
    assert auth.store.host_id().startswith("urn:uuid:")
    assert b"private-access" not in (auth.store.directory / "credentials.db").read_bytes()
    session = auth.session("chatgpt")
    assert session["sharing"] is True
    assert set(session) == {"email", "connected", "sharing", "usage_url"}
    assert auth.session("another")["connected"] is False
    host_id = auth.store.host_id()
    save_profile(auth, "__host__")
    assert auth.store.host_id() == host_id


def test_browser_sign_in_pkce_loopback_and_reauthorization(auth):
    result = auth.start_sign_in("chatgpt")
    params = parse_qs(urlsplit(result["authorization_url"]).query)
    assert params["client_id"] == [DYNAMIC_CLIENT_ID]
    assert params["agent_name_hint"] == ["k41"]
    assert params["resource"] == [RESOURCE]
    assert PLAN_SCOPE in params["scope"][0].split()
    assert params["code_challenge_method"] == ["S256"]
    uri = params["redirect_uri"][0]
    assert urlsplit(uri).hostname == "127.0.0.1"
    assert urlsplit(uri).path == "/auth/callback"
    response = httpx.get(uri, params={"state": "invalid", "code": "bad"})
    assert response.status_code == 400
    assert auth.attempt_status("chatgpt", result["attempt_id"])["status"] == "pending"
    with pytest.raises(ChatGPTAuthError, match="already in progress"):
        auth.start_sign_in("chatgpt")
    with pytest.raises(ChatGPTAuthError, match="not found"):
        auth.attempt_status("different", result["attempt_id"])
    save_profile(auth, "second")
    returning = parse_qs(urlsplit(auth.start_sign_in("second")["authorization_url"]).query)
    assert returning["client_id"] == ["oaiapp_test"]
    assert returning["id_token_hint"] == ["private-id"]
    assert "agent_name_hint" not in returning
    assert returning["ext_agent_host_id"] == params["ext_agent_host_id"]


def test_loopback_callback_completes_verified_sign_in(auth, monkeypatch, signing_key):
    result = auth.start_sign_in("chatgpt")
    attempt = auth._attempts[result["attempt_id"]]
    exchanges = configure_tokens(monkeypatch, auth, signing_key, attempt)
    response = httpx.get(attempt.redirect_uri, params=callback(attempt))
    assert response.status_code == 200
    assert auth.attempt_status("chatgpt", result["attempt_id"])["status"] == "completed"
    assert auth.access_token("chatgpt") == "new-access"
    assert exchanges[0]["client_id"] == "oaiapp_test"
    assert exchanges[0]["redirect_uri"] == attempt.redirect_uri
    assert exchanges[0]["code_verifier"] == attempt.verifier


@pytest.mark.parametrize("changes", [{"state": "wrong"}, {"error": "access_denied"}, {"client_id": ""},
                                      {"client_id": DYNAMIC_CLIENT_ID}, {"code": ""}])
def test_invalid_callbacks_never_exchange_tokens(auth, monkeypatch, changes):
    attempt = SignInAttempt("chatgpt")
    monkeypatch.setattr(auth, "_token_request", lambda _data: pytest.fail("Invalid callback exchanged tokens"))
    with pytest.raises(ChatGPTAuthError):
        auth.complete_sign_in(attempt, callback(attempt, **changes))


@pytest.mark.parametrize("claims", [{"nonce": "wrong"}, {"aud": "different"}, {"iss": "https://invalid.example"},
                                    {"exp": time.time() - 100}, {"sub": "another-account"}])
def test_unverified_or_changed_identity_never_replaces_credentials(auth, monkeypatch, signing_key, claims):
    old = save_profile(auth)
    attempt = SignInAttempt("chatgpt", client_id="oaiapp_test")
    configure_tokens(monkeypatch, auth, signing_key, attempt, **claims)
    with pytest.raises((jwt.PyJWTError, ChatGPTAuthError)):
        auth.complete_sign_in(attempt, callback(attempt))
    assert auth.store.get("chatgpt")["access_token"] == old["access_token"]


def test_registration_survives_failed_exchange_and_reauth_rejects_changed_client(auth, monkeypatch):
    attempt = SignInAttempt("chatgpt")

    def fail(_data):
        raise ChatGPTAuthError("Exchange failed")

    monkeypatch.setattr(auth, "_token_request", fail)
    with pytest.raises(ChatGPTAuthError, match="Exchange failed"):
        auth.complete_sign_in(attempt, callback(attempt))
    assert auth.store.get("chatgpt") == {"client_id": "oaiapp_test"}
    attempt.client_id = "oaiapp_original"
    with pytest.raises(ChatGPTAuthError, match="registration changed"):
        auth.complete_sign_in(attempt, callback(attempt))


def test_signature_validation_rejects_another_signer(auth, monkeypatch, signing_key):
    attempt = SignInAttempt("chatgpt")
    configure_tokens(monkeypatch, auth, signing_key, attempt)
    other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(auth._jwks, "get_signing_key_from_jwt", lambda _token: SimpleNamespace(key=other_key.public_key()))
    with pytest.raises(jwt.InvalidSignatureError):
        auth.complete_sign_in(attempt, callback(attempt))
    assert auth.session("chatgpt")["connected"] is False


def test_reauthorization_does_not_reuse_old_plan_permission(auth, monkeypatch, signing_key):
    save_profile(auth)
    attempt = SignInAttempt("chatgpt", client_id="oaiapp_test")
    configure_tokens(monkeypatch, auth, signing_key, attempt)
    exchange = auth._token_request

    def identity_only(data):
        tokens = exchange(data)
        tokens.pop("scope")
        return tokens

    monkeypatch.setattr(auth, "_token_request", identity_only)
    auth.complete_sign_in(attempt, callback(attempt))
    assert auth.session("chatgpt")["sharing"] is False
    with pytest.raises(ChatGPTAuthError, match="authorization state"):
        auth.complete_sign_in(attempt, callback(attempt))


def test_refresh_rotates_atomically_and_preserves_scopes(auth, monkeypatch):
    save_profile(auth, expires_at=0)
    refreshes = []

    def refresh(data):
        refreshes.append(data)
        time.sleep(0.03)
        return {"access_token": "rotated-access", "refresh_token": "rotated-refresh", "expires_in": 3600}

    monkeypatch.setattr(auth, "_token_request", refresh)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(auth.access_token, ["chatgpt", "chatgpt"]))
    assert results == ["rotated-access", "rotated-access"]
    assert refreshes == [{"grant_type": "refresh_token", "client_id": "oaiapp_test", "refresh_token": "private-refresh"}]
    assert auth.store.get("chatgpt")["refresh_token"] == "rotated-refresh"
    assert auth.session("chatgpt")["sharing"] is True


def test_removed_scope_blocks_inference_but_keeps_rotating_token(auth, monkeypatch):
    save_profile(auth, expires_at=0)
    monkeypatch.setattr(auth, "_token_request", lambda _data: {
        "access_token": "rotated", "refresh_token": "latest", "expires_in": 3600, "scope": "openid",
    })
    with pytest.raises(ChatGPTAuthError, match="permission was removed"):
        auth.access_token("chatgpt")
    assert auth.store.get("chatgpt")["refresh_token"] == "latest"
    assert auth.session("chatgpt")["sharing"] is False


def test_identity_only_cannot_use_plan(auth):
    save_profile(auth, scopes=["openid", "email"])
    assert auth.session("chatgpt")["connected"] is True
    assert auth.session("chatgpt")["sharing"] is False
    with pytest.raises(ChatGPTAuthError, match="not enabled"):
        auth.access_token("chatgpt")


@pytest.mark.parametrize("remote_success", [True, False])
def test_sign_out_revokes_and_clears_tokens_but_retains_registration(auth, monkeypatch, remote_success):
    save_profile(auth)
    posted = []
    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: httpx.Response(200, request=httpx.Request("GET", url),
                        json={"revocation_endpoint": f"{ISSUER}/api/accounts/oauth/revoke"}))

    def revoke(url, **kwargs):
        posted.append(kwargs["data"])
        return httpx.Response(200 if remote_success else 503)

    monkeypatch.setattr(httpx, "post", revoke)
    result = auth.sign_out("chatgpt")
    assert result["revocation_confirmed"] is remote_success
    assert posted[0] == {"token": "private-refresh", "token_type_hint": "refresh_token", "client_id": "oaiapp_test"}
    profile = auth.store.get("chatgpt")
    assert profile["client_id"] == "oaiapp_test"
    assert not any(key in profile for key in ("access_token", "refresh_token", "id_token"))
    with pytest.raises(ChatGPTAuthError, match="Continue with ChatGPT"):
        auth.access_token("chatgpt")


@pytest.mark.asyncio
async def test_model_discovery_uses_account_token_visible_slugs_and_order(auth, monkeypatch):
    save_profile(auth)
    requests = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"models": [
            {"slug": "preferred", "display_name": "Preferred model", "visibility": "list"},
            {"slug": "hidden", "visibility": "hidden"},
            {"slug": "second", "display_name": "Second model", "visibility": "list"},
        ]})

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    config = _build_provider_config("chatgpt", {"type": "chatgpt"})
    assert config.provider_type == ProviderType.CHATGPT
    assert config.api_key == ""
    factory = ChatGPTFactory(auth)
    assert await factory.list_models(config, "") == ["preferred", "second"]
    assert str(requests[0].url) == f"{RESOURCE}/models"
    assert requests[0].headers["authorization"] == "Bearer private-access"
    assert factory.model_labels["chatgpt"]["preferred"] == "Preferred model"


def response_events(terminal="completed", *, tool=False):
    output = {"id": "msg_1", "type": "message", "role": "assistant", "status": "completed",
              "content": [{"type": "output_text", "text": "Hello", "annotations": []}]}
    if tool:
        output = {"id": "fc_1", "type": "function_call", "call_id": "call_1", "name": "lookup",
                  "arguments": '{"query":"hello"}', "status": "completed"}
    response = {"id": "resp_1", "object": "response", "model": "gpt-test", "created_at": 1,
                "status": "in_progress", "output": [], "parallel_tool_calls": True,
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12,
                          "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}
    events = [
        {"type": "response.created", "response": response},
        {"type": "response.output_item.added", "output_index": 0, "item": output},
    ]
    if tool:
        events.append({"type": "response.function_call_arguments.delta", "output_index": 0,
                       "item_id": "fc_1", "delta": output["arguments"]})
    else:
        events.append({"type": "response.output_text.delta", "output_index": 0, "content_index": 0,
                       "item_id": "msg_1", "delta": "Hello"})
    if terminal:
        final = {**response, "status": terminal, "output": [output]}
        if terminal == "failed":
            final["error"] = {"code": "subscription_sharing_usage_limit_exceeded", "message": "Usage limit"}
        events.append({"type": f"response.{terminal}", "response": final})
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events)


@pytest.fixture
def model_with_stream():
    def build(terminal="completed", *, tool=False):
        requests = []

        def respond(request):
            requests.append(request)
            payload = json.loads(request.content)
            if any(item.get("role") == "system" for item in payload.get("input", [])):
                return httpx.Response(400, json={"detail": "System messages are not allowed"})
            if any(tool.get("type") in {"function", "custom"} for tool in payload.get("tools", [])):
                return httpx.Response(400, json={"detail": "Function/custom tools require namespaces or additional_tools"})
            unsupported = {"background", "conversation", "max_output_tokens", "max_tool_calls", "metadata",
                           "moderation", "multi_agent", "prompt", "prompt_cache_retention", "safety_identifier",
                           "temperature", "top_logprobs", "top_p", "truncation", "user"}
            if unsupported.intersection(payload):
                return httpx.Response(400, json={"detail": "Unsupported ChatGPT plan parameter"})
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=response_events(terminal, tool=tool))

        transport = httpx.MockTransport(respond)
        client = OpenAI(api_key="oauth-test", http_client=httpx.Client(transport=transport), max_retries=0)
        async_client = AsyncOpenAI(api_key="oauth-test", http_client=httpx.AsyncClient(transport=transport), max_retries=0)
        model = ChatGPTChatModel(model="gpt-test", api_key="placeholder", use_responses_api=True, streaming=True,
                                 root_client=client, root_async_client=async_client,
                                 client=client.chat.completions, async_client=async_client.chat.completions)
        return model, requests

    return build


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["invoke", "ainvoke", "stream", "astream"])
@pytest.mark.parametrize("terminal", ["completed", "incomplete", "failed", None])
async def test_responses_contract_and_terminal_errors(model_with_stream, mode, terminal):
    model, requests = model_with_stream(terminal)
    messages = [SystemMessage(content="Be concise."), HumanMessage(content="Say hello.")]

    async def run():
        if mode == "invoke":
            return model.invoke(messages, store=True)
        if mode == "ainvoke":
            return await model.ainvoke(messages, store=True)
        chunks = list(model.stream(messages, store=True)) if mode == "stream" else [chunk async for chunk in model.astream(messages, store=True)]
        return chunks[-1]

    if terminal == "completed":
        result = await run()
        assert result.response_metadata["status"] == "completed"
    else:
        with pytest.raises(RuntimeError, match="incomplete|Manage usage|without response.completed"):
            await run()
    request = requests[0]
    assert str(request.url) == f"{RESOURCE}/responses"
    assert request.headers["authorization"] == "Bearer oauth-test"
    payload = json.loads(request.content)
    assert payload["stream"] is True
    assert payload["store"] is False
    assert "Say hello." in json.dumps(payload["input"])
    assert payload["input"][0]["role"] == "developer"
    assert "Be concise." in json.dumps(payload["input"][0]["content"])
    assert messages[0].type == "system"


@pytest.mark.asyncio
async def test_responses_tool_calls_preserve_arguments(model_with_stream):
    model, requests = model_with_stream(tool=True)
    bound = model.bind_tools([{"type": "function", "function": {"name": "lookup", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}])
    result = await bound.ainvoke([HumanMessage(content="Search")])
    assert result.tool_calls[0]["args"] == {"query": "hello"}
    payload = json.loads(requests[0].content)
    assert "tools" not in payload
    assert payload["input"][0]["type"] == "additional_tools"
    assert payload["input"][0]["role"] == "developer"
    assert payload["input"][0]["tools"][0]["name"] == "lookup"


@pytest.mark.parametrize("content", ["Keep all instructions.", [
    {"type": "text", "text": "First instruction."}, {"type": "text", "text": "Second instruction."},
]])
def test_system_conversion_preserves_history_content_and_caller_messages(model_with_stream, content):
    model, _ = model_with_stream()
    messages = [
        SystemMessage(content=content), HumanMessage(content="First request"),
        AIMessage(content="", tool_calls=[{"name": "lookup", "args": {"query": "hello"}, "id": "call_1"}]),
        ToolMessage(content="Tool result", tool_call_id="call_1"),
        SystemMessage(content="Updated instruction."),
        ChatMessage(role="developer", content="Existing developer instruction."),
        HumanMessage(content="Follow-up request"),
    ]
    original = deepcopy(messages)
    payload = model._get_request_payload(messages)
    items = payload["input"]
    assert [item.get("role", item.get("type")) for item in items] == [
        "developer", "user", "function_call", "function_call_output", "developer", "developer", "user",
    ]
    expected_text = content if isinstance(content, str) else " ".join(block["text"] for block in content)
    serialized_text = json.dumps(items[0]["content"])
    assert all(part in serialized_text for part in expected_text.split())
    assert items[3]["call_id"] == "call_1" and items[3]["output"] == "Tool result"
    assert "Updated instruction." in json.dumps(items[4]["content"])
    assert "Existing developer instruction." in json.dumps(items[5]["content"])
    assert messages == original


@pytest.mark.asyncio
async def test_subscription_request_omits_unsupported_fields_and_normalizes_extra_body(model_with_stream):
    model, requests = model_with_stream()
    extra_input = [{"type": "message", "role": "system", "content": "Additional instruction."},
                   {"role": "user", "content": "Actual request."}]
    extra_body = {"input": extra_input, "temperature": 0.7, "store": True, "stream": False,
                  "metadata": {"source": "test"}, "truncation": "auto", "max_output_tokens": 20}
    original = deepcopy(extra_body)
    await model.ainvoke([SystemMessage(content="System instruction."), HumanMessage(content="Hello")],
                        temperature=0.8, top_p=0.9, max_tokens=10, user="test", background=False,
                        max_tool_calls=3, extra_body=extra_body)
    payload = json.loads(requests[0].content)
    assert payload["input"] == [{**extra_input[0], "role": "developer"}, extra_input[1]]
    assert not {"temperature", "top_p", "max_output_tokens", "user", "background", "max_tool_calls",
                "metadata", "truncation"}.intersection(payload)
    assert payload["store"] is False and payload["stream"] is True
    assert extra_body == original


@pytest.mark.asyncio
async def test_local_tools_and_results_keep_names_across_turns(model_with_stream):
    model, requests = model_with_stream(tool=True)
    bound = model.bind_tools([{"type": "function", "function": {"name": "lookup", "parameters": {
        "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}}}], tool_choice="lookup")
    messages = [SystemMessage(content="Use lookup to answer."), HumanMessage(content="Search")]
    response = await bound.ainvoke(messages)
    await bound.ainvoke([*messages, response, ToolMessage(content="Search result", tool_call_id=response.tool_calls[0]["id"])])
    first, second = [json.loads(request.content) for request in requests]
    assert first["tool_choice"] == second["tool_choice"] == {"type": "function", "name": "lookup"}
    assert first["input"][0] == second["input"][0]
    assert second["input"][1]["role"] == "developer"
    assert second["input"][-1] == {"type": "function_call_output", "call_id": "call_1", "output": "Search result"}
    assert any(item.get("type") == "function_call" and item.get("name") == "lookup" for item in second["input"])


def test_factory_uses_public_api_and_live_credentials(auth):
    config = ProviderConfig(name="chatgpt", provider_type=ProviderType.CHATGPT, base_url="https://invalid.example",
                            api_key="", default_model="account-model")
    model = ChatGPTFactory(auth).create(config, ModelConfig("account-model"), "")
    assert model.root_client.base_url == RESOURCE + "/"
    assert callable(model.root_client._api_key_provider)
    save_profile(auth)
    assert model.root_client._api_key_provider() == "private-access"
    save_profile(auth, access_token="another-token")
    assert model.root_client._api_key_provider() == "another-token"


def test_existing_api_key_provider_named_chatgpt_keeps_its_type():
    config = _build_provider_config("chatgpt", {"type": "openai_compatible", "api_key": "existing-key",
                                               "base_url": "https://gateway.example/v1"})
    assert config.provider_type == ProviderType.OPENAI_COMPATIBLE
    assert config.api_key == "existing-key"
