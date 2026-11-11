import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../src/lib/permissionMode.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
});
const mode = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
const store = new Map();
const storage = { getItem: (key) => store.get(key), setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) };

test("permission overrides belong to a conversation, independently of its agent", () => {
  mode.savePermissionMode(storage, "thread-1", "ask");
  mode.savePermissionMode(storage, "thread-2", "full_access");
  assert.equal(mode.readPermissionMode(storage, "thread-1"), "ask");
  assert.equal(mode.readPermissionMode(storage, "thread-2"), "full_access");
  assert.equal(mode.readPermissionMode(storage, "thread-3"), undefined);
  mode.savePermissionMode(storage, "thread-1", undefined);
  assert.equal(mode.readPermissionMode(storage, "thread-1"), undefined);
});

test("legacy or invalid stored modes never become an approval override", () => {
  for (const value of ["auto_allow", "invalid", "", null]) {
    store.set("k41-permission-mode:invalid", value);
    assert.equal(mode.readPermissionMode(storage, "invalid"), undefined);
  }
});

test("chat sends the same selection for new turns, resume and edits", async () => {
  const chat = await readFile(new URL("../src/pages/Chat.tsx", import.meta.url), "utf8");
  assert.equal((chat.match(/permission_mode: permissionMode\(\)/g) || []).length, 2);
  assert.match(chat, /const payload = buildPayload\(/);
  assert.match(chat, /savePermissionMode\(localStorage, threadId, permissionMode\(\)\)/);
  assert.match(chat, /setPermissionMode\(readPermissionMode\(localStorage, threadId/);
});

test("card payload always includes permission_mode even when inheriting", async () => {
  const form = await readFile(new URL("../src/pages/settings/agents/agentForm.ts", import.meta.url), "utf8");
  assert.match(form, /permission_mode: form.permission_mode \?\? null/);
  assert.match(form, /permission_mode: workflow === "acp_agent" \? "ask" : null/);
});

test("ACP limits and Full access effects are visible in the common picker", async () => {
  assert.match(mode.ACP_PERMISSION_LIMIT, /without a forwarded request are outside K41 enforcement/);
  assert.match(mode.FULL_ACCESS_NOTICE, /removes approval prompts for K41 tools/);
  const picker = await readFile(new URL("../src/components/PermissionModePicker.tsx", import.meta.url), "utf8");
  assert.match(picker, /ACP_PERMISSION_LIMIT/);
  assert.match(picker, /FULL_ACCESS_NOTICE/);
});
