"""Native ACP requests share K41 policy without blocking the reader thread."""

from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from agent.modules.acp.turns import AcpTurn
from agent.modules.tools.coding.models import InvocationContext, PermissionRule
from agent.modules.tools.permissions import Permissions
from agent.modules.tools.permissions.risk import RiskLevel, acp_operations, classify_acp_tool_call

OPTIONS = [{"optionId": "once", "kind": "allow_once", "name": "Once"},
           {"optionId": "always", "kind": "allow_always", "name": "Always"},
           {"optionId": "reject", "kind": "reject_once", "name": "Reject"}]


def make_turn(tmp_path, mode="ask", *, can_interrupt=True, rules=()):
    responses = []
    client = SimpleNamespace(select_option=lambda rpc, option: responses.append((rpc, option)),
                             respond_permission=lambda rpc, outcome: responses.append((rpc, outcome)))
    context = InvocationContext("agent", str(tmp_path), "thread", permission_mode=mode, permission_rules=rules)
    turn = AcpTurn(turn_id="turn", thread_id="thread", session_id="session", agent_id="provider", client=client,
                   permission_context=context, can_interrupt=can_interrupt)
    return turn, responses


@pytest.mark.parametrize("mode", ["ask", "auto", "full_access"])
@pytest.mark.parametrize("call", [{"kind": "execute", "rawInput": {"command": "rm -rf /"}},
    {"kind": "other", "rawInput": {"command": "rm -rf /"}},
    {"kind": "execute", "rawInput": {}}, {"kind": "read"}, {"kind": "edit", "locations": []}])
def test_unenforceable_and_blocked_requests_rejected_in_every_mode(tmp_path, mode, call):
    turn, responses = make_turn(tmp_path, mode)
    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})
    assert responses == [(1, "reject")] and turn.next_pending() is None
    assert "permission_denied" in str(turn.take_events())


@pytest.mark.parametrize("mode", ["ask", "auto", "full_access"])
@pytest.mark.parametrize("kind,action", [("read", "read"), ("edit", "edit"), ("execute", "shell")])
def test_rule_deny_wins_for_every_native_operation(tmp_path, mode, kind, action):
    call = {"kind": kind, "locations": [{"path": "file.txt"}], "rawInput": {"command": "echo hi"}}
    turn, responses = make_turn(tmp_path, mode, rules=(PermissionRule(action=action, effect="deny"),))
    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})
    assert responses == [(1, "reject")]


def test_all_locations_are_checked(tmp_path):
    turn, responses = make_turn(tmp_path, "full_access", rules=(PermissionRule(action="edit", resource="*secret*", effect="deny"),))
    turn.on_permission_request(1, {"toolCall": {"kind": "edit", "locations": [{"path": "ok"}, {"path": "secret"}]}, "options": OPTIONS})
    assert responses == [(1, "reject")]


def test_malformed_location_never_hides_a_second_resource(tmp_path):
    turn, responses = make_turn(tmp_path, "full_access")
    turn.on_permission_request(1, {"toolCall": {"kind": "edit", "locations": [{"path": "ok"}, {}]}, "options": OPTIONS})
    assert responses == [(1, "reject")]


def _directory_link(target, link):
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.fixture
def linked_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    outside = tmp_path / "secret"
    workspace.mkdir()
    outside.mkdir()
    (outside / "token.txt").write_text("secret", encoding="utf-8")
    _directory_link(outside, workspace / "alias")
    return workspace, outside


@pytest.mark.parametrize("mode", ["ask", "auto", "full_access"])
@pytest.mark.parametrize("kind", ["read", "edit"])
@pytest.mark.parametrize("source", ["locations", "path", "file_path", "TargetFile"])
def test_native_linked_resources_obey_deny_rules(linked_workspace, mode, kind, source):
    workspace, outside = linked_workspace
    path = "alias/token.txt"
    call = {"kind": kind, **({"locations": [{"path": path}]} if source == "locations"
                            else {"rawInput": {source: path}})}
    turn, responses = make_turn(workspace, mode, rules=(
        PermissionRule(action=kind, resource=str(outside / "*"), effect="deny"),))

    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})

    assert responses == [(1, "reject")] and turn.next_pending() is None


@pytest.mark.parametrize("path", ["alias/token.txt", "alias/new/token.txt"])
def test_native_edits_through_external_links_ask_in_auto(linked_workspace, path):
    workspace, outside = linked_workspace
    call = {"kind": "edit", "locations": [{"path": path}]}
    turn, responses = make_turn(workspace, "auto")

    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})

    assert responses == [] and turn.next_pending() is not None
    [(action, resource, risk)] = acp_operations(call, str(workspace))
    assert action == "edit" and resource == str((outside / path.removeprefix("alias/")).resolve())
    assert risk == RiskLevel.DANGEROUS


def test_native_edit_classification_resolves_workspace_alias(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    alias = tmp_path / "alias"
    _directory_link(workspace, alias)
    call = {"kind": "edit", "locations": [{"path": str(workspace / "new.txt")}]}

    assert classify_acp_tool_call(call, str(alias)) == RiskLevel.MUTATING


@pytest.mark.parametrize("kind", ["read", "search", "think", "fetch"])
def test_safe_native_requests_allow_once_in_ask(tmp_path, kind):
    turn, responses = make_turn(tmp_path)
    call = {"kind": kind, "locations": [{"path": "file.txt"}], "rawInput": {"url": "https://example.test"}}
    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})
    assert responses == [(1, "once")]


def test_dangerous_native_request_queues_without_interrupting_the_reader(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("The reader thread must never call interrupt or assert_allowed")
    monkeypatch.setattr("langgraph.types.interrupt", fail)
    monkeypatch.setattr(Permissions, "assert_allowed", fail)
    turn, responses = make_turn(tmp_path, "auto")
    turn.on_permission_request(1, {"toolCall": {"kind": "execute", "rawInput": {"command": "git reset --hard"}}, "options": OPTIONS})
    assert responses == [] and turn.next_pending() is not None
    request = turn.next_pending()
    assert turn.answer(request.request_id, "once")
    assert responses == [(1, "once")]


def test_approval_unavailable_is_visible(tmp_path):
    turn, responses = make_turn(tmp_path, can_interrupt=False)
    turn.on_permission_request(1, {"toolCall": {"kind": "edit", "locations": [{"path": "file.txt"}]}, "options": OPTIONS})
    assert responses == [(1, "reject")]
    assert "approval_unavailable" in str(turn.take_events())


def test_allow_always_only_provider_is_rejected(tmp_path):
    turn, responses = make_turn(tmp_path, "full_access")
    turn.on_permission_request(1, {"toolCall": {"kind": "think"}, "options": OPTIONS[1:]})
    assert responses == [(1, "reject")]


def test_recheck_pending_native_request_after_policy_change(tmp_path):
    turn, responses = make_turn(tmp_path)
    turn.on_permission_request(1, {"toolCall": {"kind": "edit", "locations": [{"path": "file.txt"}]}, "options": OPTIONS})
    request = turn.next_pending()
    turn.permission_context = replace(turn.permission_context, permission_rules=(PermissionRule(action="edit", effect="deny"),))
    assert turn.answer(request.request_id, "once")
    assert responses and responses[0][1] != "once"


def test_native_exact_grant_never_overrides_deny(tmp_path):
    turn, responses = make_turn(tmp_path)
    call = {"kind": "edit", "locations": [{"path": "file.txt"}], "rawInput": {"content": "first"}}
    action, resource, _ = acp_operations(call, str(tmp_path))[0]
    key = Permissions.grant_key(turn.permission_context, action, resource, {"arguments": call["rawInput"]})
    turn.native_grants.add(key[:32])
    turn.on_permission_request(1, {"toolCall": call, "options": OPTIONS})
    assert responses == [(1, "once")]
    turn.on_permission_request(2, {"toolCall": {**call, "rawInput": {"content": "second"}}, "options": OPTIONS})
    assert turn.next_pending() is not None
    turn.permission_context = replace(turn.permission_context, permission_mode="full_access",
                                      permission_rules=(PermissionRule(action="edit", effect="deny"),))
    turn.on_permission_request(3, {"toolCall": call, "options": OPTIONS})
    assert responses[-1] == (3, "reject")
