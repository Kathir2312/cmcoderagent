// Runs inside VS Code's extension host (see runTest.ts).

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import * as vscode from "vscode";
import type { CmcoderApi } from "../../src/extension";
import type { AgentEvent } from "../../src/protocol";

const events: AgentEvent[] = [];

async function until<T>(what: string, check: () => T | undefined | false, timeoutMs = 30_000): Promise<T> {
  const end = Date.now() + timeoutMs;
  for (;;) {
    const value = check();
    if (value) return value;
    if (Date.now() > end) throw new Error(`timed out waiting for ${what}; events: ${events.map((e) => e.type).join(", ")}`);
    await new Promise((r) => setTimeout(r, 100));
  }
}

const event = <T extends AgentEvent["type"]>(type: T, after = 0) =>
  until(type, () => events.slice(after).find((e) => e.type === type) as Extract<AgentEvent, { type: T }> | undefined);

function diffTabs(): vscode.Tab[] {
  return vscode.window.tabGroups.all
    .flatMap((g) => g.tabs)
    .filter((t) => t.input instanceof vscode.TabInputTextDiff && t.input.modified.scheme === "cmcoder-diff");
}

/** A screenshot of the screen, if CMCODER_GATE_SHOTS names a folder (the release build keeps them for the guides). Never fails the test. */
async function shot(name: string): Promise<void> {
  const folder = process.env.CMCODER_GATE_SHOTS;
  if (!folder) return;
  await new Promise((r) => setTimeout(r, 1500)); // the page and the window settle
  mkdirSync(folder, { recursive: true });
  const r = spawnSync("import", ["-window", "root", join(folder, `vscode-${name}.png`)]);
  if (r.status !== 0) console.warn(`screenshot ${name} failed: ${r.error ?? r.stderr}`);
}

export async function run(): Promise<void> {
  const ext = vscode.extensions.getExtension("cmcoder.cmcoder");
  assert.ok(ext, "extension not found");
  const api = (await ext.activate()) as CmcoderApi;
  api.onEvent((e) => events.push(e));
  const commands = await vscode.commands.getCommands(true);
  for (const c of [
    "cmcoder.newConversation",
    "cmcoder.acceptChange",
    "cmcoder.openTerminal",
    "cmcoder.askAboutSelection",
    "cmcoder.setupCodeSearch",
    "cmcoder.updateIndex",
    "cmcoder.codeSearch",
    "cmcoder.openNavigator",
  ]) {
    assert.ok(commands.includes(c), `command ${c} missing`);
  }

  // Opening the chat view starts cmcoder for the workspace.
  await vscode.commands.executeCommand("cmcoder.focus");
  const init = await event("system_init");
  // Code search isn't set up in the test workspace: the status bar says so.
  const codeSearch = await event("index_status");
  assert.equal(codeSearch.set_up, false);
  const folder = vscode.workspace.workspaceFolders![0].uri;
  assert.equal(vscode.Uri.file(init.cwd).fsPath.toLowerCase(), folder.fsPath.toLowerCase());

  // 1. An edit is reviewed in the diff editor and accepted there.
  const app = vscode.Uri.joinPath(folder, "app.py");
  await vscode.window.showTextDocument(app);
  let mark = events.length;
  assert.ok(api.send("change x to 2"));
  const ask = await event("permission_request", mark);
  assert.equal(ask.name, "Edit");
  assert.deepEqual([ask.change?.before, ask.change?.after], ["x = 1\n", "x = 2\n"]);
  await until("the diff editor", () => diffTabs().length === 1);
  await shot("2-diff");
  await vscode.commands.executeCommand("cmcoder.acceptChange");
  const edited = await until("the Edit result", () =>
    events.slice(mark).find((e) => e.type === "tool_result" && e.name === "Edit"),
  );
  assert.equal((edited as Extract<AgentEvent, { type: "tool_result" }>).is_error, false, JSON.stringify(edited));
  const done = await event("result", mark);
  assert.equal(done.result, "Changed x to 2.");
  assert.equal(readFileSync(app.fsPath, "utf8"), "x = 2\n");
  await until("the diff to close", () => diffTabs().length === 0);

  // 2. The model asks VS Code for the file's problems.
  const problems = vscode.languages.createDiagnosticCollection("test");
  problems.set(app, [new vscode.Diagnostic(new vscode.Range(0, 0, 0, 1), "x is never used", vscode.DiagnosticSeverity.Warning)]);
  mark = events.length;
  assert.ok(api.send("any problems?"));
  const tool = await until("the getDiagnostics result", () =>
    events.slice(mark).find((e) => e.type === "tool_result" && e.name === "getDiagnostics"),
  );
  assert.match((tool as Extract<AgentEvent, { type: "tool_result" }>).content, /app\.py:1:1 warning: x is never used/);
  assert.equal((await event("result", mark)).result, "Checked the problems.");
  await shot("1-chat");
  problems.dispose();

  // 3. The Agent Navigator opens as an editor tab, and its page runs: its script
  // reports "ready" (a tab alone can be empty, as when navigator.js wasn't packaged).
  let navigatorReady = false;
  const readyListener = api.onNavigatorReady(() => (navigatorReady = true));
  await vscode.commands.executeCommand("cmcoder.openNavigator");
  await until("the Agent Navigator tab", () =>
    vscode.window.tabGroups.all.some((g) => g.tabs.some((t) => t.label.endsWith("Agent Navigator"))),
  );
  await until("the Agent Navigator's script", () => navigatorReady);
  await shot("3-navigator");
  readyListener.dispose();
}
