// The Agent Navigator (dist/navigator.js) in a real browser: events in, the
// mind map and messages to the extension out. Needs `npm run build` first.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { chromium, type Browser, type Page } from "playwright";
import type { AgentEvent, SubagentStatus } from "../src/protocol";
import type { FromNavigator, ToNavigator } from "../src/webviewMessages";

const root = join(__dirname, "..");
let browser: Browser;

before(async () => {
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
});

async function navigator(): Promise<{
  page: Page;
  send: (m: ToNavigator) => Promise<void>;
  ev: (e: AgentEvent) => Promise<void>;
  sent: () => Promise<FromNavigator[]>;
}> {
  const page = await browser.newPage({ viewport: { width: 1200, height: 700 } });
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setContent(`<!DOCTYPE html><html><head><style>${readFileSync(join(root, "media", "navigator.css"), "utf8")}</style>
    <script>window.sent=[];function acquireVsCodeApi(){return{postMessage:(m)=>window.sent.push(m)}}</script>
    </head><body><div id="app"></div></body></html>`);
  await page.addScriptTag({ content: readFileSync(join(root, "dist", "navigator.js"), "utf8") });
  const send = (m: ToNavigator) =>
    page.evaluate(
      (m) =>
        new Promise<void>((resolve) => {
          const done = (e: MessageEvent) => {
            if ((e.data as { testFlush?: boolean })?.testFlush) {
              window.removeEventListener("message", done);
              resolve();
            }
          };
          window.addEventListener("message", done);
          window.postMessage(m, "*");
          window.postMessage({ testFlush: true }, "*");
        }),
      m,
    );
  const ev = (event: AgentEvent) => send({ kind: "event", event });
  const sent = () => page.evaluate(() => (window as unknown as { sent: FromNavigator[] }).sent);
  page.on("close", () => assert.deepEqual(errors, []));
  return { page, send, ev, sent };
}

function status(id: string, number: number, description: string, state: SubagentStatus["state"], extra: Partial<SubagentStatus> = {}): AgentEvent {
  return {
    type: "subagent_status", id, number, description, agent_type: "explore", model: "qwen-small",
    state, steps: 3, max_steps: 100, tool_uses: 4, tokens: 31400, elapsed_ms: 72000, activity: "", ...extra,
  };
}

const task = (id: string, d: string): AgentEvent => ({
  type: "tool_use", id, name: "Task", input: { description: d }, label: `Task(explore: ${d})`, parent_tool_use_id: null,
});
const step = (parent: string, id: string, label: string): AgentEvent => ({
  type: "tool_use", id, name: "Read", input: {}, label, parent_tool_use_id: parent,
});

test("navigator: asks for the turn so far, and shows an empty turn", async () => {
  const { page, send, sent } = await navigator();
  assert.deepEqual(await sent(), [{ kind: "ready" }]);
  await send({ kind: "reset", prompt: "analyse each project", model: "Qwen3.5-35B-A3B", busy: true });
  const root = (await page.textContent(".node.root")) ?? "";
  assert.match(root, /main agent/);
  assert.match(root, /Qwen3\.5-35B-A3B/);
  assert.match(root, /“analyse each project”/);
  assert.match(root, /working…/);
  assert.match((await page.textContent(".empty")) ?? "", /No subagents in this turn yet/);
  await page.close();
});

test("navigator: subagents as branches, their last tool calls as leaves, details and Stop", async () => {
  const { page, send, ev, sent } = await navigator();
  await send({ kind: "reset", prompt: "fan out", model: "m", busy: true });
  await ev(task("a", "Analyse TW.Core"));
  await ev(task("b", "Analyse TW.Data"));
  await ev({ type: "tool_use", id: "own", name: "Glob", input: {}, label: "Glob(*.sln)", parent_tool_use_id: null });
  for (const [i, label] of ["Glob(a)", "Read(b.cs)", "Read(c.cs)", "Read(d.cs)", "Read(e.cs)"].entries()) await ev(step("a", `a${i}`, label));
  await ev(step("b", "b1", "Read(Db.cs)"));
  await ev({ type: "tool_result", id: "b1", name: "Read", content: "No such file\nmore", is_error: true, summary: null, parent_tool_use_id: "b" });
  await ev(status("a", 1, "Analyse TW.Core", "running", { activity: "Read(e.cs)" }));
  await ev(status("b", 2, "Analyse TW.Data", "done"));
  await ev({ type: "tool_result", id: "b", name: "Task", content: "Data layer: EF Core.", is_error: false, summary: "1 tool use", parent_tool_use_id: null });

  assert.equal(await page.locator(".node.agent").count(), 2);
  assert.match((await page.textContent(".node.agent.running")) ?? "", /◐ 1\. Analyse TW\.Core.*explore · 3\/100 steps.*4 tools · 31\.4k tokens · 1m1[23]s/);
  assert.match((await page.textContent(".node.agent.done")) ?? "", /✓ 2\. Analyse TW\.Data/);
  // TW.Core: the last 3 of 5 steps, the rest folded; the current one marked.
  const leaves = await page.locator(".node.leaf").allTextContents();
  assert.deepEqual(leaves, ["… 2 earlier steps", "Read(c.cs)", "Read(d.cs)", "Read(e.cs)", "Read(Db.cs) — No such file"]);
  assert.equal(await page.textContent(".node.leaf.current"), "Read(e.cs)");
  assert.equal(await page.locator(".node.leaf.problem").count(), 1);
  // One link per subagent and per leaf; the main agent's own calls are counted.
  assert.equal(await page.locator("svg path.link").count(), 2 + 5);
  assert.match((await page.textContent(".node.root")) ?? "", /1 tool call of its own/);
  assert.match((await page.textContent("header .summary")) ?? "", /2 subagents · 1 active/);

  // Stop on the running one only.
  assert.equal(await page.locator(".node.agent.done .stop").count(), 0);
  await page.click(".node.agent.running .stop");
  assert.deepEqual((await sent()).filter((m) => m.kind === "stopSubagent"), [{ kind: "stopSubagent", id: "a" }]);

  // Click a subagent: its steps and report.
  await page.click(".node.agent.done");
  assert.equal(await page.isVisible(".details"), true);
  assert.deepEqual(await page.locator(".details .steps li").allTextContents(), ["Read(Db.cs)No such file"]);
  assert.equal(await page.textContent(".details .report"), "Data layer: EF Core.");
  await page.click(".details .close");
  assert.equal(await page.isVisible(".details"), false);

  // The turn ends: what still ran counts as stopped; a new turn starts over.
  await ev({ type: "result", subtype: "interrupted", is_error: false, result: "", num_turns: 1, duration_ms: 1, usage: {}, session_id: "s", review: null });
  assert.equal(await page.locator(".node.agent.stopped").count(), 1);
  assert.match((await page.textContent(".node.root")) ?? "", /interrupted/);
  await page.click("header .chat");
  assert.deepEqual((await sent()).at(-1), { kind: "openChat" });
  await send({ kind: "reset", prompt: "next", model: "m", busy: true });
  assert.equal(await page.locator(".node.agent").count(), 0);
  await page.close();
});
