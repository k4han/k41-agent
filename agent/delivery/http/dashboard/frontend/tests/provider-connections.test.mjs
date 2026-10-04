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

const routes = await loadModule("../src/lib/providerRoutes.ts");
const drafts = await loadModule("../src/lib/settingsDrafts.ts");

test("provider tabs support direct details, refresh, and unknown queries", () => {
  assert.equal(routes.currentProviderTab("/settings/providers", ""), "llm");
  for (const tab of ["llm", "web", "workspace", "decision"]) {
    assert.equal(routes.currentProviderTab("/settings/providers", `?tab=${tab}`), tab);
    assert.equal(routes.currentProviderTab(`/settings/providers/${tab}/example`, "?tab=other"), tab);
  }
  assert.equal(routes.currentProviderTab("/settings/providers", "?tab=web&new=tavily"), "web");
  assert.equal(routes.currentProviderTab("/settings/providers", "?tab=unknown"), "llm");
});

test("connection creation returns only to supported local forms", () => {
  for (const path of ["/settings/tools", "/settings/agents", "/settings/agents/new", "/settings/agents/research"]) {
    assert.equal(routes.connectionReturnTo(path), path);
  }
  for (const path of ["https://example.com", "//example.com", "/login", "/settings/agents/../security"]) {
    assert.equal(routes.connectionReturnTo(path), "/settings/providers?tab=web");
  }
});

test("connection detours keep independent drafts without sharing mutable objects", () => {
  const tool = { provider: "firecrawl", connection: "team" };
  drafts.keepSettingsDraft("tools", tool);
  drafts.keepSettingsDraft("agent:research", { connection: "separate" });
  tool.connection = "changed-after-navigation";
  assert.deepEqual(drafts.takeSettingsDraft("tools"), { provider: "firecrawl", connection: "team" });
  assert.equal(drafts.takeSettingsDraft("tools"), undefined);
  assert.deepEqual(drafts.takeSettingsDraft("agent:research"), { connection: "separate" });
});

const connections = await loadModule("../src/lib/providerConnections.ts");

test("provider names use the first free suffix without case collisions", () => {
  assert.equal(connections.suggestProviderName("google", []), "google");
  assert.equal(connections.suggestProviderName("google", ["Google", "GOOGLE-2"]), "google-3");
  assert.equal(connections.suggestProviderName("google", ["google", "google-3"]), "google-2");
  assert.equal(connections.suggestProviderName("google", ["google", "google_2"], true), "google-3");
  assert.equal(connections.suggestProviderName("default", []), "default-2");
});
