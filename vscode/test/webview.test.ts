// The chat panel (dist/webview.js) in a real browser, with a stand-in for the
// VS Code API: events in, rendered DOM and messages to the extension out.
// Needs `npm run build` first and a Chromium for Playwright
// (`npx playwright install chromium`, or PLAYWRIGHT_BROWSERS_PATH).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { chromium, type Browser, type Page } from "playwright";
import type { AgentEvent } from "../src/protocol";
import type { FromWebview, ToWebview } from "../src/webviewMessages";

const root = join(__dirname, "..");
let browser: Browser;

before(async () => {
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
});

async function panel(): Promise<{ page: Page; send: (m: ToWebview) => Promise<void>; ev: (e: AgentEvent) => Promise<void>; sent: () => Promise<FromWebview[]> }> {
  const page = await browser.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setContent(`<!DOCTYPE html><html><head><style>${readFileSync(join(root, "media", "chat.css"), "utf8")}</style>
    <script>window.sent=[];function acquireVsCodeApi(){return{postMessage:(m)=>window.sent.push(m)}}</script>
    </head><body><div id="app"></div></body></html>`);
  await page.addScriptTag({ content: readFileSync(join(root, "dist", "webview.js"), "utf8") });
  const send = (m: ToWebview) => page.evaluate((m) => window.postMessage(m, "*"), m);
  const ev = (event: AgentEvent) => send({ kind: "event", event });
  const sent = () => page.evaluate(() => (window as unknown as { sent: FromWebview[] }).sent);
  page.on("close", () => assert.deepEqual(errors, []));
  await send({ kind: "state", state: "ready" });
  await ev({
    type: "system_init",
    protocol_version: 1,
    session_id: "s",
    cwd: "/p",
    model: "qwen3-27b",
    provider: "corp",
    tools: [],
    permission_mode: "default",
  });
  return { page, send, ev, sent };
}

const result: AgentEvent = {
  type: "result",
  subtype: "success",
  is_error: false,
  result: "",
  num_turns: 1,
  duration_ms: 1,
  usage: {},
  session_id: "s",
};

test("a turn: prompt, streamed Markdown, tool card, todo list", async () => {
  const { page, ev, sent } = await panel();
  await page.fill("textarea", "fix it");
  await page.press("textarea", "Enter");
  assert.deepEqual((await sent()).at(-1), { kind: "send", text: "fix it", includeContext: false });
  assert.equal(await page.isVisible(".stop"), true);

  await ev({ type: "todo_update", todos: [{ content: "Fix", status: "in_progress" }, { content: "Test", status: "pending" }] });
  await ev({ type: "assistant_delta", text: "Done **now**" });
  await ev({ type: "assistant_delta", text: " <img src=x onerror=alert(1)>" });
  await ev({ type: "assistant_message", text: "Done **now** <img src=x onerror=alert(1)>", reasoning: "", tool_calls: [{}], model: null });
  await ev({ type: "tool_use", id: "1", name: "Bash", input: {}, label: "Bash(pytest -q)" });
  await ev({ type: "tool_result", id: "1", name: "Bash", content: "a\nb\nc\nd\ne", is_error: false, summary: "exit 0" });
  await ev(result);

  assert.equal(await page.textContent(".todos"), "Todo list · 0/2 done► Fix☐ Test");
  assert.equal(await page.innerHTML(".msg.assistant strong"), "now");
  assert.equal(await page.locator(".msg.assistant img").count(), 0, "raw HTML from the model is not rendered");
  assert.match((await page.textContent(".tool")) ?? "", /Bash\(pytest -q\)└ exit 0a\nb\nc\n… 2 more lines/);
  assert.equal(await page.isVisible(".send"), true);
  await page.close();
});

test("a permission request: diff, deny with feedback, no duplicate note", async () => {
  const { page, send, ev, sent } = await panel();
  await send({ kind: "context", label: "app.py:3-5" });
  await page.fill("textarea", "edit");
  await page.press("textarea", "Enter");
  assert.equal(((await sent()).at(-1) as { includeContext: boolean }).includeContext, true);
  await ev({
    type: "permission_request",
    request_id: "r1",
    tool_use_id: "t1",
    name: "Edit",
    label: "Edit(app.py)",
    input: { file_path: "app.py", old_string: "a", new_string: "b" },
    suggested_rule: "Edit(app.py)",
    reason: "",
    can_remember: true,
    change: { path: "/p/app.py", before: "a", after: "b" },
  });
  assert.match((await page.textContent(".permission pre")) ?? "", /- a\n\+ b/);
  await page.click("text=Show diff");
  await page.fill(".permission input", "keep a");
  await page.click("text=Deny");
  await ev({ type: "permission_denied", id: "t1", name: "Edit", reason: "denied by user" });
  const out = await sent();
  assert.deepEqual(out.slice(-2), [
    { kind: "showDiff", requestId: "r1" },
    { kind: "permission", requestId: "r1", allow: false, remember: false, feedback: "keep a" },
  ]);
  assert.equal(await page.locator(".note.warn").count(), 0);
  assert.match((await page.textContent(".permission")) ?? "", /└ Denied/);
  await page.close();
});

test("high-risk requests have no 'Always'; answers from the diff editor close the card", async () => {
  const { page, send, ev } = await panel();
  await ev({
    type: "permission_request",
    request_id: "r2",
    tool_use_id: "t2",
    name: "Bash",
    label: "Bash(rm -rf build)",
    input: { command: "rm -rf build" },
    suggested_rule: "Bash(rm:*)",
    reason: "high-risk command",
    can_remember: false,
    change: null,
  });
  assert.equal(await page.locator("text=Always allow").count(), 0);
  await send({ kind: "permissionAnswered", requestId: "r2", text: "Allowed" });
  assert.match((await page.textContent(".permission")) ?? "", /└ Allowed/);
  assert.equal(await page.locator(".permission button").count(), 0);
  await page.close();
});

test("history, past conversations and resume", async () => {
  const { page, ev, sent } = await panel();
  await ev({ type: "history", messages: [{ role: "user", text: "hi" }, { role: "tool", text: "Read(a.py)" }, { role: "assistant", text: "**hello**" }] });
  assert.equal(await page.textContent(".msg.user"), "hi");
  assert.equal(await page.innerHTML(".msg.assistant strong"), "hello");
  await page.click("text=History");
  assert.deepEqual((await sent()).at(-1), { kind: "listSessions" });
  await ev({ type: "session_list", sessions: [{ id: "abc", title: "Earlier work", updated: 1790000000, messages: 3 }] });
  await page.click("text=Earlier work");
  assert.deepEqual((await sent()).at(-1), { kind: "resume", id: "abc" });
  await page.close();
});
