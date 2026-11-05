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
const { toolCallSummary } = await loadModule("../src/lib/toolCallSummary.ts");

const acpCard = { name: "ag", graph_type: "acp_agent" };
const reactCard = { name: "kode", graph_type: "react_agent" };

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

test("install progress is readable in every phase", () => {
  const job = { state: "running", phase: "downloading", version: "1.0", downloaded_bytes: 10485760, total_bytes: 104857600, error: null };
  assert.equal(acp.installProgressLabel(job), "Downloading 10 of 100 MB…");
  assert.equal(acp.installProgressLabel({ ...job, total_bytes: null }), "Downloading 10 MB…");
  assert.equal(acp.installProgressLabel({ ...job, phase: "extracting" }), "Unpacking…");
  assert.equal(acp.installProgressLabel({ ...job, state: "succeeded", phase: "installed" }), "Installed version 1.0.");
  assert.equal(acp.installProgressLabel({ ...job, state: "succeeded", phase: "up_to_date" }), "Already up to date (1.0).");
  assert.equal(acp.installProgressLabel({ ...job, state: "failed", phase: "failed", error: "No space." }), "No space.");
});
