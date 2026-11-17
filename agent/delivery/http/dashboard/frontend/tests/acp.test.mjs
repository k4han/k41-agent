import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function loadModule(path) {
  const source = await readFile(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}

const acp = await loadModule("../src/lib/acp.ts");
const { API_PATHS } = await loadModule("../src/lib/endpoints.ts");
const { toolCallSummary } = await loadModule("../src/lib/toolCallSummary.ts");

const acpCard = { name: "ag", graph_type: "acp_agent" };
const reactCard = { name: "kode", graph_type: "react_agent" };

test("CLI status URLs carry agent and workspace as encoded parameters", () => {
  const path = API_PATHS.acpAgent("antigravity-cli", " custom/agent ", "D:\\My Projects\\workspace");
  const url = new URL(path, "http://localhost");
  assert.equal(url.searchParams.get("agent_name"), "custom/agent");
  assert.equal(url.searchParams.get("cwd"), "D:\\My Projects\\workspace");
  assert.equal(API_PATHS.acpAgent("antigravity-cli"), "/dashboard-api/acp/agents/antigravity-cli");
});

test("CLI option choices are separated by agent and workspace", () => {
  const settings = { agent: "antigravity-cli", agent_name: "reviewer" };
  const scope = acp.acpOptionScope("card", settings, "/workspace");
  assert.notEqual(scope, acp.acpOptionScope("card", { ...settings, agent_name: "coder" }, "/workspace"));
  assert.notEqual(scope, acp.acpOptionScope("card", settings, "/other"));
  assert.equal(acp.acpOptionScope("card", { agent: "antigravity-cli", agent_name: " " }),
    acp.acpOptionScope("card", { agent: "antigravity-cli", agent_name: null }));
});

test("a new conversation can pick any agent", () => {
  assert.deepEqual(acp.selectableAgentCards([acpCard, reactCard], reactCard, false), [acpCard, reactCard]);
});

test("a started conversation stays on its side of the ACP boundary", () => {
  assert.deepEqual(acp.selectableAgentCards([acpCard, reactCard], reactCard, true), [reactCard]);
  assert.deepEqual(acp.selectableAgentCards([acpCard, reactCard], acpCard, true), [acpCard]);
});

test("only ACP questions yield an ACP request id", () => {
  assert.equal(acp.acpPermissionRequestId("acp_permission:abc"), "abc");
  assert.equal(acp.acpPermissionRequestId("permission:abc"), null);
});

test("tool summaries prefer the path and ignore other tools", () => {
  assert.equal(toolCallSummary("acp_edit", { title: "Run create_file?", paths: ["a.txt"] }), "a.txt");
  assert.equal(toolCallSummary("acp_execute", { title: "echo hi" }), "echo hi");
  assert.equal(toolCallSummary("bash", { title: "echo hi" }), "bash");
  assert.equal(toolCallSummary("acp_read", null), "acp_read");
});

const thinking = {
  id: "thinking_level", name: "Thinking Level", category: "thought_level", current_value: "high",
  options: [{ value: "low", name: "Low" }, { value: "medium", name: "Medium" }, { value: "high", name: "High" }],
};

test("session options prefer the chat choice, then the card, then the agent default", () => {
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], { thinking_level: "low" }, { thinking_level: "medium" }), { thinking_level: "low" });
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], {}, { thinking_level: "medium" }), { thinking_level: "medium" });
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], {}, {}), { thinking_level: "high" });
});

test("session options skip values the agent no longer offers", () => {
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], { thinking_level: "retired" }, { thinking_level: "medium" }), { thinking_level: "medium" });
  assert.deepEqual(acp.resolveAcpOptionValues([{ ...thinking, current_value: null }], { thinking_level: "retired" }, {}), {});
});

test("session options never carry a choice the agent did not list", () => {
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], { mode: "yolo", thinking_level: "low" }, {}), { thinking_level: "low" });
});

test("legacy card choices seed a CLI scope without overwriting newer or empty choices", (t) => {
  const previousWindow = globalThis.window;
  let stored = JSON.stringify({ card: { model: "legacy", thinking_level: "retired", invalid: 7 } });
  globalThis.window = { localStorage: { getItem: () => stored, setItem: (_key, value) => { stored = value; } } };
  t.after(() => { globalThis.window = previousWindow; });
  const scope = acp.acpOptionScope("card", { agent: "antigravity-cli" }, "/workspace");
  assert.deepEqual(acp.readAcpOptionChoices(scope, "card"), { model: "legacy", thinking_level: "retired" });
  assert.deepEqual(JSON.parse(stored)[scope], { model: "legacy", thinking_level: "retired" });
  assert.equal(JSON.parse(stored).card.model, "legacy");
  acp.writeAcpOptionChoices(scope, { model: "new" });
  assert.deepEqual(acp.readAcpOptionChoices(scope, "card"), { model: "new" });
  acp.writeAcpOptionChoices(scope, {});
  assert.deepEqual(acp.readAcpOptionChoices(scope, "card"), {});
  const named = acp.acpOptionScope("card", { agent: "antigravity-cli", agent_name: "reviewer" }, "/workspace");
  assert.deepEqual(acp.readAcpOptionChoices(named), {});
  assert.deepEqual(acp.resolveAcpOptionValues([thinking], { thinking_level: "retired" }, {}), { thinking_level: "high" });
});
