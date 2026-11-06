"""Scriptable ACP agent for tests. Standard library only; run with ``python -I -u``.

The default shapes mirror what agy_acp_server 1.4.5 was observed to send, including
its quirks. They are written by hand, so no captured traffic or credentials exist here.

Usage: fake_agent.py <state_dir> [--no-resume] [--no-load] [--require-auth] [--mcp-http] [--mcp-on-new]
The scenario is chosen per prompt from the prompt text: "scenario:<name>".
Every received message is appended to <state_dir>/calls.jsonl.
"""

import http.server
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

STATE = Path(sys.argv[1])
FLAGS = set(sys.argv[2:])
STATE.mkdir(parents=True, exist_ok=True)
AUTH_MARKER = STATE / "authenticated"
out_lock = threading.Lock()
responses = {}
cancels = {}
next_id = [1000]
session_mcp_servers = {}


def send(message):
    with out_lock:
        sys.stdout.buffer.write(json.dumps(message).encode("utf-8") + b"\n")
        sys.stdout.buffer.flush()


def update(session_id, body):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": session_id, "update": body}})


def chunk(session_id, text):
    update(session_id, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": text}})


def client_request(method, params):
    next_id[0] += 1
    rid = next_id[0]
    waiter = threading.Event()
    responses[rid] = {"event": waiter}
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
    waiter.wait()
    return responses.pop(rid)["message"]


def config_options():
    return [
        {"id": "model", "name": "Model", "category": "model", "type": "select", "currentValue": "fake-pro",
         "options": [{"value": "fake-pro", "name": "Fake Pro"}, {"value": "fake-lite", "name": "Fake Lite"}]},
        {"id": "thinking_level", "name": "Thinking Level", "category": "thought_level", "type": "select",
         "currentValue": "high", "options": [{"value": "low", "name": "Low"}, {"value": "high", "name": "High"}]},
        {"id": "mode", "name": "Session Mode", "category": "mode", "type": "select", "currentValue": "default",
         "options": [{"value": "default", "name": "Default"}, {"value": "yolo", "name": "Yolo"}]},
    ]


def permission(session_id, tool_call, kind="execute"):
    options = [{"optionId": "allow", "name": "Allow", "kind": "allow_once"},
               {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]
    if kind == "execute":
        options.insert(0, {"optionId": "allow_always", "name": "Allow Always (risky)", "kind": "allow_always",
                           "_meta": {"agy.security.warning": {"message": "Allowing always can be risky."}}})
    message = client_request("session/request_permission", {"sessionId": session_id, "toolCall": tool_call, "options": options})
    outcome = message.get("result", {}).get("outcome", {})
    return outcome.get("optionId") if outcome.get("outcome") == "selected" else None


def run_edit(session_id, tag="1"):
    """Approved edits run under a second id; the original is later reported as never executed."""
    original = f"edit-{tag}"
    call = {"toolCallId": original, "title": "Run create_file?", "kind": "edit", "status": "pending",
            "rawInput": {"TargetFile": "hello.txt"}, "locations": [{"path": "hello.txt"}]}
    update(session_id, {"sessionUpdate": "tool_call", **call})
    choice = permission(session_id, call, kind="edit")
    if choice is None:
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": original, "status": "failed",
                            "rawOutput": "Tool call was approved but never executed."})
        return "cancelled"
    if choice == "deny":
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": original, "status": "failed", "rawOutput": "Rejected by user"})
        return "denied"
    real = f"{session_id}:{tag}"
    update(session_id, {"sessionUpdate": "tool_call", "toolCallId": real, "title": "Running edit_file", "kind": "edit",
                        "status": "in_progress", "rawInput": {"file_path": "hello.txt"}, "locations": [{"path": "hello.txt"}]})
    (STATE / "effects.log").open("a", encoding="utf-8").write(f"edit-{tag}\n")
    update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": real, "status": "completed", "rawOutput": "Create hello.txt"})
    return original


def mcp_request(session_id, method, params=None, *, token=None):
    """POST one JSON-RPC request as agy_acp_server does. Returns (status, parsed body)."""
    server = session_mcp_servers[session_id][0]
    headers = {item["name"]: item["value"] for item in server["headers"]}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    headers.update({"Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}).encode()
    request = urllib.request.Request(server["url"], data=body, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


def mcp_tool_text(reply):
    result = reply["result"]
    text = " ".join(block.get("text", "") for block in result["content"])
    return ("ERROR " if result.get("isError") else "") + text


def mcp_tool_call_fields(tool, arguments):
    """The shape agy_acp_server gives a client-provided MCP tool."""
    return {"title": f"k41_{tool}", "kind": "other", "rawInput": {"arguments": arguments, **arguments},
            "_meta": {"mcp": {"tool": tool, "server": "k41"}, "is_mcp_tool_call": True}}


def run_mcp_tool(session_id, call_id, tool, arguments, sent_arguments=None, report_input=True):
    """Ask, call the tool if allowed, and report it with the misleading rawOutput seen in practice."""
    fields = mcp_tool_call_fields(tool, arguments)
    if not report_input:
        # rawInput is optional in ACP.
        del fields["rawInput"]
    update(session_id, {"sessionUpdate": "tool_call", "toolCallId": call_id, "status": "pending", **fields})
    options = [{"optionId": "allow_always", "name": "Allow Always", "kind": "allow_always"},
               {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
               {"optionId": "deny", "name": "Deny", "kind": "reject_once"}]
    message = client_request("session/request_permission", {
        "sessionId": session_id, "options": options,
        "toolCall": {"toolCallId": call_id, "status": "pending", **fields},
    })
    outcome = message.get("result", {}).get("outcome", {})
    (STATE / "mcp_choices.log").open("a", encoding="utf-8").write(f"{outcome.get('optionId')}\n")
    if outcome.get("outcome") != "selected" or outcome.get("optionId") == "deny":
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "failed", "rawOutput": "Rejected by user"})
        return "denied"
    update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "in_progress", **fields})
    _, reply = mcp_request(session_id, "tools/call", {"name": tool, "arguments": sent_arguments if sent_arguments is not None else arguments})
    update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": call_id, "status": "completed", "rawOutput": f"Call {tool} tool"})
    return mcp_tool_text(reply)


def turn_body(session_id, name, cancel, text):
    if name == "mcp_list":
        _, reply = mcp_request(session_id, "tools/list")
        chunk(session_id, json.dumps(reply["result"]["tools"]))
        return "end_turn"
    if name == "mcp_call":
        chunk(session_id, run_mcp_tool(session_id, "m1", "get_current_time", {}))
        return "end_turn"
    if name == "mcp_call_without_input":
        chunk(session_id, run_mcp_tool(session_id, "m1", "get_current_time", {}, report_input=False))
        return "end_turn"
    if name == "mcp_call_twice":
        first = run_mcp_tool(session_id, "m1", "get_current_time", {})
        second = run_mcp_tool(session_id, "m2", "get_current_time", {})
        chunk(session_id, f"{bool(first)} {bool(second)}")
        return "end_turn"
    if name == "mcp_swapped_arguments":
        chunk(session_id, run_mcp_tool(session_id, "m1", "get_current_time", {"a": 1}, sent_arguments={"a": 2}))
        return "end_turn"
    if name == "mcp_unapproved":
        _, reply = mcp_request(session_id, "tools/call", {"name": "get_current_time", "arguments": {}})
        chunk(session_id, mcp_tool_text(reply))
        return "end_turn"
    if name == "mcp_control_tool":
        chunk(session_id, run_mcp_tool(session_id, "m1", "ask_user", {"questions": []}))
        return "end_turn"
    if name == "mcp_handoff":
        chunk(session_id, run_mcp_tool(session_id, "m1", "browser_handoff", {"reason": "Sign in"}))
        return "end_turn"
    if name == "mcp_bad_token":
        status, _ = mcp_request(session_id, "tools/list", token="not-the-token")
        chunk(session_id, f"status={status}")
        return "end_turn"
    if name == "mcp_get":
        server = session_mcp_servers[session_id][0]
        headers = {item["name"]: item["value"] for item in server["headers"]}
        try:
            with urllib.request.urlopen(urllib.request.Request(server["url"], headers=headers), timeout=20) as reply:
                chunk(session_id, f"status={reply.status}")
        except urllib.error.HTTPError as exc:
            chunk(session_id, f"status={exc.code}")
        return "end_turn"
    if name == "text":
        chunk(session_id, "Hello ")
        chunk(session_id, "world")
        update(session_id, {"sessionUpdate": "usage_update", "used": 123, "size": 1000})
        return "end_turn"
    if name == "echo":
        chunk(session_id, text)
        return "end_turn"
    if name == "tools":
        chunk(session_id, "Running.")
        update(session_id, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "echo hi", "kind": "execute",
                            "status": "in_progress", "rawInput": {"CommandLine": "echo hi"}})
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
                            "rawOutput": {"combinedOutput": "hi\r\n", "exitCode": 0}})
        chunk(session_id, "Done.")
        return "end_turn"
    if name == "ends_on_tool":
        update(session_id, {"sessionUpdate": "tool_call", "toolCallId": "t1", "title": "echo hi", "kind": "execute", "status": "in_progress"})
        update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed", "rawOutput": "hi"})
        return "end_turn"
    if name == "permission":
        chunk(session_id, "Before. ")
        original = run_edit(session_id)
        if original == "cancelled":
            return "cancelled"
        chunk(session_id, "After.")
        if original != "denied":
            update(session_id, {"sessionUpdate": "tool_call_update", "toolCallId": original, "status": "failed",
                                "rawOutput": "Tool call was approved but never executed."})
        return "end_turn"
    if name == "two_permissions":
        chunk(session_id, "One. ")
        run_edit(session_id, "1")
        chunk(session_id, "Two. ")
        run_edit(session_id, "2")
        chunk(session_id, "Three.")
        return "end_turn"
    if name == "concurrent_permissions":
        calls = [{"toolCallId": f"c{i}", "title": f"step {i}", "kind": "execute", "status": "pending"} for i in (1, 2)]
        results = {}
        workers = [threading.Thread(target=lambda c=c: results.__setitem__(c["toolCallId"], permission(session_id, c))) for c in calls]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        chunk(session_id, json.dumps(results, sort_keys=True))
        return "end_turn"
    if name == "slow":
        for index in range(600):
            if cancel.wait(0.05):
                chunk(session_id, "The request was cancelled by the client.")
                return "cancelled"
            chunk(session_id, f"w{index} ")
        return "end_turn"
    if name == "ignore_cancel":
        chunk(session_id, "Stuck. ")
        time.sleep(60)
        return "end_turn"
    if name == "crash":
        chunk(session_id, "Partial. ")
        time.sleep(0.05)
        os._exit(3)
    if name == "noise":
        with out_lock:
            sys.stdout.buffer.write(b"Opening in existing browser session.\n{broken\n[1,2]\n")
            sys.stdout.buffer.flush()
        chunk(session_id, "Clean.")
        return "end_turn"
    if name == "bad_stop":
        chunk(session_id, "Quota message.")
        return "error"
    if name == "unknown_method":
        reply = client_request("fs/read_text_file", {"sessionId": session_id, "path": "x"})
        chunk(session_id, f"code={reply.get('error', {}).get('code')}")
        return "end_turn"
    if name == "stderr":
        sys.stderr.write("Open the following link to authenticate the ACP server: https://example.test/secret\n")
        sys.stderr.flush()
        chunk(session_id, "ok")
        return "end_turn"
    chunk(session_id, f"unknown scenario {name}")
    return "end_turn"


def run_oauth(rid):
    """Mirrors the real flow: link on stderr, and a listener that accepts any callback."""
    state = uuid.uuid4().hex
    outcome = {}
    finished = threading.Event()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            outcome["ok"] = query.get("state") == [state] and bool(query.get("code"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Authentication complete")
            finished.set()

        def log_message(self, *_args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    redirect = urllib.parse.quote(f"http://127.0.0.1:{server.server_address[1]}/", safe="")
    sys.stderr.write(
        "Open the following link to authenticate the ACP server: "
        f"https://accounts.google.com/o/oauth2/v2/auth?redirect_uri={redirect}&state={state}\n"
    )
    sys.stderr.flush()
    finished.wait(60)
    server.shutdown()
    if outcome.get("ok"):
        AUTH_MARKER.write_text("yes", encoding="utf-8")
        send({"jsonrpc": "2.0", "id": rid, "result": {"_meta": {"antigravity": {"tier": {"name": "Fake Pro"}}}}})
    else:
        AUTH_MARKER.unlink(missing_ok=True)
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Onboarding failed", "data": {"reason": "onboarding_failed"}}})


def prompt_text(params):
    return " ".join(block.get("text", "") for block in params.get("prompt", []) if block.get("type") == "text")


def handle_prompt(rid, params):
    session_id = params["sessionId"]
    text = prompt_text(params)
    name = "echo"
    for word in text.split():
        if word.startswith("scenario:"):
            name = word.split(":", 1)[1]
    cancel = cancels.setdefault(session_id, threading.Event())
    cancel.clear()
    stop = turn_body(session_id, name, cancel, text)
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": stop, "usage": {"totalTokens": 10}}})


def known_session(session_id):
    return (STATE / f"session-{session_id}").exists()


def session_setup_payload():
    return {"configOptions": config_options(), "modes": {"currentModeId": "default", "availableModes": []}}


def main():
    calls = (STATE / "calls.jsonl").open("a", encoding="utf-8")
    for raw in sys.stdin.buffer:
        message = json.loads(raw)
        calls.write(json.dumps(message) + "\n")
        calls.flush()
        method, rid, params = message.get("method"), message.get("id"), message.get("params") or {}
        if method is None:
            entry = responses.get(rid)
            if entry:
                entry["message"] = message
                entry["event"].set()
            continue
        if method == "initialize":
            capabilities = {"loadSession": "--no-load" not in FLAGS,
                            "promptCapabilities": {"image": True},
                            "auth": {"logout": {}},
                            "sessionCapabilities": {} if "--no-resume" in FLAGS else {"resume": {}},
                            "_meta": {"antigravity": {"systemPrompt": {"append": True}}}}
            if "--mcp-http" in FLAGS:
                capabilities["mcpCapabilities"] = {"http": True, "sse": True}
            send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": 1, "agentCapabilities": capabilities,
                "agentInfo": {"name": "fake", "title": "Fake", "version": "0.0.0"},
                "authMethods": [{"id": "oauth-personal", "name": "Log in with Google"},
                                {"id": "gemini-api-key", "name": "Gemini API key"}]}})
        elif method == "authenticate":
            if params.get("methodId") == "gemini-api-key":
                if os.environ.get("GEMINI_API_KEY") != "good-key":
                    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Invalid API key"}})
                    continue
                AUTH_MARKER.write_text("yes", encoding="utf-8")
                send({"jsonrpc": "2.0", "id": rid, "result": {}})
            elif "--oauth" in FLAGS:
                threading.Thread(target=run_oauth, args=(rid,), daemon=True).start()
            else:
                AUTH_MARKER.write_text("yes", encoding="utf-8")
                send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "logout":
            AUTH_MARKER.unlink(missing_ok=True)
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
        elif method == "session/new":
            if "--require-auth" in FLAGS and not AUTH_MARKER.exists():
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": "Authentication required"}})
                continue
            session_id = str(uuid.uuid4())
            session_mcp_servers[session_id] = params.get("mcpServers") or []
            if "--mcp-on-new" in FLAGS and session_mcp_servers[session_id]:
                # Connecting this early is allowed, although agy_acp_server waits for the first prompt.
                _, reply = mcp_request(session_id, "tools/list")
                listed = [tool["name"] for tool in reply["result"]["tools"]]
                (STATE / "mcp_tools_on_new.json").write_text(json.dumps(listed), encoding="utf-8")
            (STATE / f"session-{session_id}").write_text(params.get("cwd", ""), encoding="utf-8")
            send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": session_id, **session_setup_payload()}})
            update(session_id, {"sessionUpdate": "available_commands_update", "availableCommands": []})
        elif method in {"session/resume", "session/load"}:
            session_id = params["sessionId"]
            if not known_session(session_id):
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "Session not found"}})
                continue
            session_mcp_servers[session_id] = params.get("mcpServers") or []
            if method == "session/load":
                update(session_id, {"sessionUpdate": "user_message_chunk", "content": {"type": "text", "text": "old prompt"}})
                chunk(session_id, "old reply")
            send({"jsonrpc": "2.0", "id": rid, "result": session_setup_payload()})
        elif method == "session/set_config_option":
            valid = {"model": {"fake-pro", "fake-lite"}, "thinking_level": {"low", "high"}, "mode": {"default", "yolo"}}
            if params.get("value") not in valid.get(params.get("configId"), set()):
                send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32602, "message": "Invalid value"}})
                continue
            options = config_options()
            for option in options:
                if option["id"] == params["configId"]:
                    option["currentValue"] = params["value"]
            send({"jsonrpc": "2.0", "id": rid, "result": {"configOptions": options}})
        elif method == "session/prompt":
            threading.Thread(target=handle_prompt, args=(rid, params), daemon=True).start()
        elif method == "session/cancel":
            cancels.setdefault(params.get("sessionId"), threading.Event()).set()
        else:
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Method not found"}})
    # Mirrors the real server, which ignores stdin EOF and keeps running.
    if "--exit-on-eof" not in FLAGS:
        time.sleep(600)


main()
