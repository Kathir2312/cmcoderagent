// The chat panel (dist/chat.js) in a real browser, once per way an IDE connects
// (src/bridge.ts): events in, rendered DOM and messages to the IDE out.
// Needs `npm run build` first and a Chromium for Playwright
// (`npx playwright install chromium`, or PLAYWRIGHT_BROWSERS_PATH).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { chromium, type Browser, type Page } from "playwright";
import type { AgentEvent } from "../src/protocol";
import type { FromWebview, ToWebview } from "../src/messages";

const root = join(__dirname, "..");
let browser: Browser;

before(async () => {
  browser = await chromium.launch();
});

after(async () => {
  await browser?.close();
});

// How each IDE connects (src/bridge.ts). JetBrains and Eclipse add their function
// only after the page has loaded, so messages wait in the panel's queue first.
const FLAVOURS = ["vscode", "webview2", "function"] as const;
type Flavour = (typeof FLAVOURS)[number];
const HOSTS: Record<Flavour, string> = {
  vscode: "window.sent=[];function acquireVsCodeApi(){return{postMessage:(m)=>window.sent.push(m)}}",
  webview2: "window.sent=[];window.chrome={webview:{postMessage:(m)=>window.sent.push(m)}}",
  function: "window.sent=[];setTimeout(()=>{window.cmcoderHostPost=(j)=>window.sent.push(JSON.parse(j))},150)",
};

/** Waits until the host can receive (the late function in JetBrains/Eclipse) and the queue is sent. */
async function connected(page: Page, flavour: Flavour): Promise<void> {
  if (flavour !== "function") return;
  // Every page says "ready" first: once it arrived, the queue has been sent.
  await page.waitForFunction(() => (window as unknown as { sent: unknown[] }).sent.length > 0);
}

async function panel(flavour: Flavour, product?: string): Promise<{ page: Page; send: (m: ToWebview) => Promise<void>; ev: (e: AgentEvent) => Promise<void>; sent: () => Promise<FromWebview[]> }> {
  const page = await browser.newPage();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setContent(`<!DOCTYPE html><html><head><style>${readFileSync(join(root, "dist", "chat.css"), "utf8")}</style>
    <script>${HOSTS[flavour]}</script>
    </head><body data-icon="https://panel.test/icon.png"${product ? ` data-product="${product}"` : ""}><div id="app"></div></body></html>`);
  await page.addScriptTag({ content: readFileSync(join(root, "dist", "chat.js"), "utf8") });
  await connected(page, flavour);
  // postMessage is delivered later: resolve only once the page has handled it
  // (a marker posted right after it arrives after it), so assertions never
  // race the rendering.
  const send = (m: ToWebview) =>
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
    critique: false,
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
  review: null,
};

for (const flavour of FLAVOURS) {
  test(`${flavour}: a turn: prompt, streamed Markdown, tool card, todo list`, async () => {
    const { page, ev, sent } = await panel(flavour);
    await page.fill("textarea", "fix it");
    await page.press("textarea", "Enter");
    assert.deepEqual((await sent()).at(-1), { kind: "send", text: "fix it", includeContext: false });
    assert.equal(await page.isVisible(".stop"), true);

    await ev({ type: "todo_update", todos: [{ content: "Fix", status: "in_progress" }, { content: "Test", status: "pending" }] });
    await ev({ type: "assistant_delta", text: "Done **now**" });
    await ev({ type: "assistant_delta", text: " <img src=x onerror=alert(1)>" });
    await ev({ type: "assistant_message", text: "Done **now** <img src=x onerror=alert(1)>", reasoning: "", tool_calls: [{}], model: null });
    await ev({ type: "tool_use", id: "1", name: "Bash", input: {}, label: "Bash(pytest -q)", parent_tool_use_id: null });
    await ev({ type: "tool_result", id: "1", name: "Bash", content: "a\nb\nc\nd\ne", is_error: false, summary: "exit 0", parent_tool_use_id: null });
    await ev(result);

    assert.equal(await page.textContent(".todos"), "Todo list · 0/2 done► Fix☐ Test");
    assert.equal(await page.innerHTML(".msg.assistant strong"), "now");
    assert.equal(await page.locator(".msg.assistant img").count(), 0, "raw HTML from the model is not rendered");
    assert.match((await page.textContent(".tool")) ?? "", /Bash\(pytest -q\)└ exit 0a\nb\nc\n… 2 more lines/);
    assert.equal(await page.isVisible(".send"), true);
    await page.close();
  });

  test(`${flavour}: a permission request: diff, deny with feedback, no duplicate note`, async () => {
    const { page, send, ev, sent } = await panel(flavour);
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
    await ev({ type: "permission_denied", id: "t1", name: "Edit", reason: "denied by user", parent_tool_use_id: null });
    const out = await sent();
    assert.deepEqual(out.slice(-2), [
      { kind: "showDiff", requestId: "r1" },
      { kind: "permission", requestId: "r1", allow: false, remember: false, feedback: "keep a" },
    ]);
    assert.equal(await page.locator(".note.warn").count(), 0);
    assert.match((await page.textContent(".permission")) ?? "", /└ Denied/);
    await page.close();
  });

  test(`${flavour}: high-risk requests have no 'Always'; answers from the diff editor close the card`, async () => {
    const { page, send, ev } = await panel(flavour);
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

  test(`${flavour}: history, past conversations and resume`, async () => {
    const { page, ev, sent } = await panel(flavour);
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

  test(`${flavour}: model output can't inject markup: allowlist after escaping`, async () => {
    const { page, ev } = await panel(flavour);
    const evil = [
      "[click](javascript:alert(1)) [ok](https://example.com)",
      "<script>window.pwned=1</script><iframe src=x></iframe>",
      "![img](https://tracker.example/p.png)",
      "- [x] done",
      "| a | b |\n|:-|-:|\n| 1 | 2 |",
    ].join("\n\n");
    await ev({ type: "assistant_message", text: evil, reasoning: "", tool_calls: [], model: null });
    const html = await page.innerHTML(".msg.assistant");
    assert.doesNotMatch(html, /<(script|iframe|img)\b/i);
    assert.doesNotMatch(html, /javascript:/i);
    assert.match(html, /<a href="https:\/\/example\.com">ok<\/a>/);
    assert.match(html, /<input[^>]*type="checkbox"/);
    assert.match(html, /<td[^>]*align="right"[^>]*>2<\/td>/);
    assert.equal(await page.evaluate(() => (window as unknown as { pwned?: number }).pwned), undefined);
    await page.close();
  });

  test(`${flavour}: slash-command completion: list, filter, Tab to complete, then send`, async () => {
    const { page, ev, sent } = await panel(flavour);
    await page.type("textarea", "/");
    assert.deepEqual((await sent()).at(-1), { kind: "listCommands" });
    await ev({
      type: "command_list",
      commands: [
        { name: "compact", description: "Summarise", argument_hint: "[focus]", origin: "built-in" },
        { name: "review", description: "Review a <b>file</b>", argument_hint: "<file>", origin: "project" },
        { name: "release:notes", description: "Notes", argument_hint: "", origin: "user" },
      ],
    });
    assert.equal(await page.locator(".commands .command").count(), 3);
    await page.type("textarea", "re");
    assert.equal(await page.locator(".commands .command").count(), 2);
    assert.equal(await page.textContent(".commands .selected"), "/review <file>Review a <b>file</b> (project)");
    await page.press("textarea", "ArrowDown");
    await page.press("textarea", "Tab");
    assert.equal(await page.inputValue("textarea"), "/release:notes ");
    assert.equal(await page.isHidden(".commands"), true);
    await page.type("textarea", "v2");
    await page.press("textarea", "Enter");
    assert.deepEqual((await sent()).at(-1), { kind: "send", text: "/release:notes v2", includeContext: false });
    await page.close();
  });

  test(`${flavour}: a subagent's steps are listed inside its Task card`, async () => {
    const { page, ev } = await panel(flavour);
    await page.fill("textarea", "find it");
    await page.press("textarea", "Enter");
    await ev({ type: "tool_use", id: "t", name: "Task", input: {}, label: "Task(explore: find the parser)", parent_tool_use_id: null });
    await ev({ type: "tool_use", id: "c1", name: "Grep", input: {}, label: "Grep(parse)", parent_tool_use_id: "t" });
    await ev({ type: "tool_result", id: "c1", name: "Grep", content: "3 files", is_error: false, summary: null, parent_tool_use_id: "t" });
    await ev({ type: "tool_use", id: "c2", name: "Read", input: {}, label: "Read(x.py)", parent_tool_use_id: "t" });
    await ev({ type: "tool_result", id: "c2", name: "Read", content: "No such file\nmore", is_error: true, summary: null, parent_tool_use_id: "t" });
    await ev({ type: "tool_result", id: "t", name: "Task", content: "Found it.", is_error: false, summary: "2 tool uses", parent_tool_use_id: null });
    await ev(result);
    assert.equal(await page.locator(".tool").count(), 1, "one card: the subagent's steps are inside it");
    assert.deepEqual(await page.locator(".tool .steps .step").allTextContents(), ["● Grep(parse)", "● Read(x.py) — No such file"]);
    assert.match((await page.textContent(".tool")) ?? "", /└ 2 tool uses/);
    assert.equal(await page.textContent(".tool details.steps summary"), "2 subagent steps");
    assert.equal(await page.isVisible(".tool .steps .step"), false, "folded once the Task is done");
    await page.close();
  });

  test(`${flavour}: rewind: the conversation is redrawn and the message is back in the input`, async () => {
    const { page, ev } = await panel(flavour);
    await ev({ type: "history", messages: [{ role: "user", text: "first" }, { role: "user", text: "second" }] });
    assert.equal(await page.locator(".msg.user").count(), 2);
    await ev({ type: "rewind_points", points: [{ turn: 2, text: "second", files_changed: 1, outside_files: [] }] });
    assert.match((await page.textContent(".log")) ?? "", /list at the top of the window/);
    await ev({
      type: "rewound",
      code: true,
      conversation: true,
      actions: [{ path: "/p/new.txt", action: "deleted (created after that point)" }],
      prompt: "second",
    });
    await ev({ type: "history", messages: [{ role: "user", text: "first" }] });
    assert.deepEqual(await page.locator(".msg.user").allTextContents(), ["first"]);
    assert.equal(await page.inputValue("textarea"), "second");
    assert.match((await page.textContent(".log")) ?? "", /Rewound the conversation\. Files: 0 restored, 1 deleted/);
    await page.close();
  });

  test(`${flavour}: branding: the product's name and logo before the first message`, async () => {
    const { page, ev } = await panel(flavour, "Acme Coder");
    assert.equal(await page.getAttribute("textarea", "placeholder"), "Ask Acme Coder… (Enter to send, Shift+Enter for a new line)");
    const empty = () =>
      page.$eval(".log", (log) => ({
        name: getComputedStyle(log, "::after").content,
        logo: getComputedStyle(log, "::before").backgroundImage,
      }));
    assert.deepEqual(await empty(), { name: '"Acme Coder"', logo: (await empty()).logo });
    assert.match((await empty()).logo, /icon\.png/);
    await ev({ type: "assistant_message", text: "Hello.", reasoning: "", tool_calls: [], model: "m" });
    assert.equal((await empty()).name, "none"); // gone once the conversation starts
    const avatar = await page.$eval(".msg.assistant", (m) => getComputedStyle(m, "::before").backgroundImage);
    assert.match(avatar, /icon\.png/);
    await page.close();
  });

  test(`${flavour}: branding: defaults to cmcoder`, async () => {
    const { page } = await panel(flavour);
    assert.match((await page.getAttribute("textarea", "placeholder")) ?? "", /^Ask cmcoder…/);
    await page.close();
  });

  test(`${flavour}: code search: the code added from the index is noted in the panel`, async () => {
    const { page, ev } = await panel(flavour);
    await ev({
      type: "code_context",
      items: [
        { path: "auth.py", start_line: 4, end_line: 8, symbol: "refresh_auth_token", score: 0.82 },
        { path: "notes.md", start_line: 1, end_line: 3, symbol: null, score: 0.5 },
      ],
      tokens: 310,
    });
    const text = await page.textContent(".log");
    assert.match(text ?? "", /Added code from the index: auth\.py:4-8, notes\.md:1-3 \(≈310 tokens\)/);
    await page.close();
  });

  test(`${flavour}: the agent map: live subagent rows, Stop sends stop_subagent, cards show the status`, async () => {
    const { page, ev, sent } = await panel(flavour);
    await page.fill("textarea", "one subagent per project");
    await page.press("textarea", "Enter");
    const status = (id: string, number: number, description: string, state: string, extra = {}) =>
      ev({
        type: "subagent_status", id, number, description, agent_type: "explore", model: "qwen-small",
        state: state as never, steps: 3, max_steps: 100, tool_uses: 4, tokens: 31400, elapsed_ms: 72000,
        activity: "", ...extra,
      });
    for (const [id, n, d] of [["a", 1, "Analyse TW.Core"], ["b", 2, "Analyse TW.Api"]] as const) {
      await ev({ type: "tool_use", id, name: "Task", input: {}, label: `Task(explore: ${d})`, parent_tool_use_id: null });
      await status(id, n, d, "running", { activity: "Read(Program.cs)" });
    }
    await status("c", 3, "Analyse TW.Web", "queued");
    assert.equal(await page.isVisible(".agents"), true);
    const rows = await page.locator(".agents .agent .name").allTextContents();
    assert.deepEqual(rows, ["├─ ◐ 1. Analyse TW.Core", "├─ ◐ 2. Analyse TW.Api", "└─ ○ 3. Analyse TW.Web"]);
    assert.match((await page.textContent(".agents .agent.running .detail")) ?? "", /explore · 3\/100 steps · 4 tools · 31\.4k tokens · 1m1[23]s/);
    assert.equal(await page.locator(".agents .activity").count(), 2);
    assert.match((await page.textContent(".agents .title")) ?? "", /3 active · 0 finished/);

    await page.click(".agents .open-map");
    assert.deepEqual((await sent()).filter((m) => m.kind === "openNavigator"), [{ kind: "openNavigator" }]);
    await page.locator(".agents .agent").nth(1).locator(".stop-one").click();
    assert.deepEqual((await sent()).filter((m) => m.kind === "stopSubagent"), [{ kind: "stopSubagent", id: "b" }]);

    await status("b", 2, "Analyse TW.Api", "stopped");
    await ev({ type: "tool_result", id: "b", name: "Task", content: "(stopped) partial", is_error: false, summary: "4 tool uses", parent_tool_use_id: null });
    assert.match((await page.locator(".tool").nth(1).textContent()) ?? "", /■ stopped · explore · 3\/100 steps/);
    assert.equal(await page.locator(".agents .agent.stopped .stop-one").count(), 0);
    assert.match((await page.textContent(".agents .title")) ?? "", /2 active · 1 finished/);

    await ev(result); // the turn ended: the rest count as stopped, the map goes
    assert.equal(await page.isVisible(".agents"), false);
    assert.match((await page.locator(".tool").first().textContent()) ?? "", /■ stopped/);
    await page.close();
  });

  test(`${flavour}: progress line while working, and messages queued meanwhile are sent when the turn ends`, async () => {
    const { page, ev, sent } = await panel(flavour);
    await page.fill("textarea", "convert the files");
    await page.press("textarea", "Enter");
    assert.equal(await page.isVisible(".progress"), true);
    assert.match((await page.textContent(".progress .verb")) ?? "", /^(Considering|Pondering|Thinking|Working|Reasoning|Exploring|Analysing|Composing)…$/);
    assert.match((await page.textContent(".progress .meta")) ?? "", /\(\d+s · Esc to interrupt\)/);
    const spark1 = await page.textContent(".progress .spark");
    await page.waitForTimeout(300);
    assert.notEqual(await page.textContent(".progress .spark"), spark1, "the spark is animated");

    await ev({ type: "reasoning_delta", text: "hmm" });
    assert.equal(await page.textContent(".progress .verb"), "Thinking…");
    await ev({ type: "tool_use", id: "1", name: "Bash", input: {}, label: "Bash(dotnet build)", parent_tool_use_id: null });
    assert.equal(await page.textContent(".progress .verb"), "Running…");
    assert.equal(await page.textContent(".progress .detail"), "Bash(dotnet build)");
    await ev({ type: "usage", prompt_tokens: 5000, completion_tokens: 1234, estimated: false, cost: null, context_window: 32768 });
    assert.match((await page.textContent(".progress .meta")) ?? "", /↓ 1\.2k tokens/);

    // Typing meanwhile queues; nothing is sent until the turn ends.
    assert.match((await page.getAttribute("textarea", "placeholder")) ?? "", /^Queue another message/);
    await page.fill("textarea", "also update the README");
    await page.press("textarea", "Enter");
    await page.fill("textarea", "and the docs");
    await page.press("textarea", "Enter");
    assert.deepEqual(await page.locator(".queued .item .text").allTextContents(), ["↳ also update the README", "↳ and the docs"]);
    assert.equal((await sent()).filter((m) => m.kind === "send").length, 1);

    await ev(result);
    const sends = (await sent()).filter((m) => m.kind === "send");
    assert.equal(sends.length, 2);
    assert.equal((sends[1] as { text: string }).text, "also update the README\n\nand the docs");
    assert.equal(await page.isVisible(".queued"), false);
    assert.equal(await page.isVisible(".progress"), true, "the queued message started a new turn");
    await ev(result);
    assert.equal(await page.isVisible(".progress"), false);
    await page.close();
  });

  test(`${flavour}: an interrupted turn gives queued messages back to edit`, async () => {
    const { page, ev, sent } = await panel(flavour);
    await page.fill("textarea", "start");
    await page.press("textarea", "Enter");
    await page.fill("textarea", "next thing");
    await page.press("textarea", "Enter");
    await ev({ ...result, subtype: "interrupted" } as AgentEvent);
    assert.equal(await page.inputValue("textarea"), "next thing");
    assert.equal((await sent()).filter((m) => m.kind === "send").length, 1);
    await page.close();
  });

  test(`${flavour}: critique: the reviewer's findings while it fixes, then the verdict; the progress line says so`, async () => {
    const { page, ev } = await panel(flavour);
    await page.fill("textarea", "fix add");
    await page.press("textarea", "Enter");
    await ev({ type: "tool_use", id: "r1", name: "Task", input: { description: "Review the answer (round 1/2)", subagent_type: "critic" }, label: "Review(the answer, round 1/2)", parent_tool_use_id: null });
    assert.equal(await page.textContent(".progress .verb"), "Reviewing the answer…");
    await ev({
      type: "review_result", round: 1, max_rounds: 2, verdict: "fail", final: false, summary: "Wrong file.",
      issues: [{ severity: "high", problem: "names a.py, the code is in b.py", where: "the answer", fix: "say b.py" }],
    });
    assert.equal(await page.textContent(".progress .verb"), "Fixing what the reviewer found…");
    const fixing = (await page.locator(".note.review").first().textContent()) ?? "";
    assert.match(fixing, /↺ The reviewer found 1 problem; fixing it \(review 1\/2\):/);
    assert.match(fixing, /\[high\] names a\.py, the code is in b\.py \(the answer\)fix: say b\.py/);
    await ev({ type: "assistant_message", text: "Corrected answer.", reasoning: "", tool_calls: [], model: null });
    await ev({ type: "review_result", round: 2, max_rounds: 2, verdict: "pass", final: true, summary: "Now right.", issues: [] });
    assert.equal(await page.locator(".note.review").nth(1).textContent(), "✓ Reviewed: Now right.");
    await ev({ type: "review_result", round: 2, max_rounds: 2, verdict: "fail", final: true, summary: "x", issues: [{ severity: "low", problem: "p", where: "", fix: "" }] });
    assert.match((await page.locator(".note.review").nth(2).textContent()) ?? "", /⚠ Not validated: after 2 reviews the reviewer still found 1 problem:\[low\] p/);
    await ev(result);
    await page.close();
  });

  test(`${flavour}: critique checkbox: shows the state, switches it for every window, follows other windows`, async () => {
    const { page, send, ev, sent } = await panel(flavour);
    const box = page.locator("header .critique input");
    assert.equal(await box.isChecked(), false);
    assert.equal(await box.isEnabled(), true);
    await box.check();
    assert.deepEqual((await sent()).at(-1), { kind: "setCritique", enabled: true });
    await ev({ type: "critique_changed", enabled: true, source: "you" });
    assert.equal(await box.isChecked(), true);
    assert.equal(await page.locator(".note").count(), 0, "your own click needs no note");
    // Switched off in another window (or the terminal): the box follows, with a note.
    await ev({ type: "critique_changed", enabled: false, source: "saved" });
    assert.equal(await box.isChecked(), false);
    assert.equal(await page.textContent(".note.info"), "Critique off (switched in another window).");
    // cmcoder stopped: the box can't be used until it's back.
    await send({ kind: "state", state: "exited", message: "stopped" });
    assert.equal(await box.isDisabled(), true);
    await ev({ type: "system_init", protocol_version: 1, session_id: "t", cwd: "/p", model: "m", provider: "corp", tools: [], permission_mode: "default", critique: true });
    assert.equal(await box.isEnabled(), true);
    assert.equal(await box.isChecked(), true);
    await page.close();
  });
}
