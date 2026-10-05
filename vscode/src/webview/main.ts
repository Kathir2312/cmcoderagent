// The chat panel (runs in the webview, a sandboxed browser page).
// Plain DOM code: it receives protocol events from the extension and renders them.

import { marked } from "marked";
import type {
  AgentEvent,
  CommandInfo,
  PermissionDenied,
  PermissionRequest,
  ReviewResult,
  SubagentStatus,
  ToolResult,
  ToolUse,
} from "../protocol";
import type { FromWebview, ToWebview } from "../webviewMessages";

declare function acquireVsCodeApi(): { postMessage(message: FromWebview): void };
const vscode = acquireVsCodeApi();
const post = (m: FromWebview) => vscode.postMessage(m);

const MODES = ["default", "acceptEdits", "plan", "bypassPermissions"];
const MARKS: Record<string, string> = { pending: "☐", in_progress: "►", completed: "☑" };

// Model output is Markdown; any raw HTML in it is shown as text, never run.
marked.use({
  gfm: true,
  breaks: false,
  renderer: {
    html: ({ text }) => escapeHtml(text),
  },
});

// Defence in depth on top of the escaping above and the page's CSP: rendered
// Markdown is parsed into an inert document (no scripts run, nothing loads),
// and only these elements and attributes are kept.
const ALLOWED_TAGS = new Set([
  "P", "BR", "HR", "H1", "H2", "H3", "H4", "H5", "H6", "STRONG", "EM", "DEL", "S",
  "CODE", "PRE", "BLOCKQUOTE", "UL", "OL", "LI", "A", "TABLE", "THEAD", "TBODY",
  "TR", "TH", "TD", "INPUT", "SPAN",
]);
const ALLOWED_ATTRS: Record<string, string[]> = {
  A: ["href", "title"],
  OL: ["start"],
  TH: ["align"],
  TD: ["align"],
  INPUT: ["type", "checked", "disabled"],
  CODE: ["class"],
};

function sanitize(parent: Node): void {
  for (const node of Array.from(parent.childNodes)) {
    if (node.nodeType === Node.TEXT_NODE) continue;
    if (!(node instanceof Element) || !ALLOWED_TAGS.has(node.tagName)) {
      // Unknown elements become their text; comments and the rest go.
      const text = node instanceof Element ? node.getAttribute("alt") ?? node.textContent ?? "" : "";
      node.replaceWith(document.createTextNode(text));
      continue;
    }
    const keep = ALLOWED_ATTRS[node.tagName] ?? [];
    for (const attr of Array.from(node.attributes)) {
      if (!keep.includes(attr.name)) node.removeAttribute(attr.name);
    }
    if (node.tagName === "A" && !/^(https?:|mailto:|#)/i.test(node.getAttribute("href") ?? "")) {
      node.removeAttribute("href");
    }
    if (node.tagName === "INPUT" && node.getAttribute("type") !== "checkbox") {
      node.replaceWith(document.createTextNode(""));
      continue;
    }
    sanitize(node);
  }
}

function setMarkdown(target: HTMLElement, markdown: string): void {
  const html = marked.parse(markdown, { async: false }) as string;
  const doc = new DOMParser().parseFromString(html, "text/html");
  sanitize(doc.body);
  target.replaceChildren(...Array.from(doc.body.childNodes));
}

// --- layout ------------------------------------------------------------------

// The product name (from the extension's displayName; see brand.ts).
const PRODUCT = document.body.dataset.product || "cmcoder";

const app = document.getElementById("app")!;
app.innerHTML = `
  <header>
    <span class="model" title="Model"></span>
    <button class="history secondary" title="Past conversations in this project">History</button>
    <select class="mode" title="Permission mode (Shift+Tab in the CLI)"></select>
  </header>
  <section class="sessions" hidden></section>
  <section class="todos" hidden></section>
  <main class="log" aria-live="polite"></main>
  <section class="agents" hidden></section>
  <div class="progress" hidden role="status"><span class="spark">✻</span><span class="verb"></span><span class="detail"></span><span class="meta"></span></div>
  <div class="status" hidden></div>
  <footer>
    <div class="queued" hidden></div>
    <label class="context" hidden title="Send the active file, selection and problems with this message">
      <input type="checkbox" checked> <span></span>
    </label>
    <div class="commands" role="listbox" hidden></div>
    <textarea rows="3"></textarea>
    <div class="actions">
      <button class="attach secondary" title="Attach a file (@)">@</button>
      <span class="usage"></span>
      <button class="stop secondary" hidden title="Stop (Esc)">Stop</button>
      <button class="send">Send</button>
    </div>
  </footer>`;

const $ = <T extends HTMLElement>(sel: string) => app.querySelector(sel) as T;
const log = $<HTMLElement>(".log");
log.dataset.product = PRODUCT; // the empty screen shows the logo and this name
const input = $<HTMLTextAreaElement>("textarea");
input.placeholder = `Ask ${PRODUCT}… (Enter to send, Shift+Enter for a new line)`;
const sendButton = $<HTMLButtonElement>(".send");
const stopButton = $<HTMLButtonElement>(".stop");
const modeSelect = $<HTMLSelectElement>(".mode");
const modelLabel = $<HTMLElement>(".model");
const usageLabel = $<HTMLElement>(".usage");
const statusLine = $<HTMLElement>(".status");
const progressLine = $<HTMLElement>(".progress");
const queuedBox = $<HTMLElement>(".queued");
const todosPanel = $<HTMLElement>(".todos");
const agentsPanel = $<HTMLElement>(".agents");
const sessionsPanel = $<HTMLElement>(".sessions");
const historyButton = $<HTMLButtonElement>(".history");
const attachButton = $<HTMLButtonElement>(".attach");
const contextChip = $<HTMLLabelElement>(".context");
const commandsPopup = $<HTMLElement>(".commands");
const contextBox = contextChip.querySelector("input") as HTMLInputElement;
const contextText = contextChip.querySelector("span") as HTMLElement;

for (const mode of MODES) modeSelect.append(new Option(mode, mode));

// --- state -------------------------------------------------------------------

let busy = false;
let rewound: string | undefined; // the note to show once the rewound history is drawn
let ready = false;
let reply: { el: HTMLElement; text: string } | undefined; // the streaming reply
let renderQueued = false;
const toolCards = new Map<string, HTMLElement>();
const permissionCards = new Map<string, HTMLElement>();
// Tool calls the user denied here: their card already says so.
const deniedHere = new Set<string>();

function setBusy(value: boolean): void {
  const was = busy;
  busy = value;
  sendButton.hidden = value;
  stopButton.hidden = !value;
  input.placeholder = value
    ? "Queue another message… (Esc to stop)"
    : `Ask ${PRODUCT}… (Enter to send, Shift+Enter for a new line)`;
  if (!value) showStatus();
  if (value && !was) startProgress();
  if (!value) stopProgress();
}

// --- the progress line: "✻ Considering… (12s · ↓ 1.2k tokens · Esc to interrupt)" ---

const SPARKS = ["·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢"];
const VERBS = ["Considering", "Pondering", "Thinking", "Working", "Reasoning", "Exploring", "Analysing", "Composing"];
// What the agent is doing, from the tool it runs.
const TOOL_VERBS: Record<string, string> = {
  Read: "Reading", Write: "Writing", Edit: "Editing", MultiEdit: "Editing", Bash: "Running",
  Grep: "Searching", Glob: "Searching", CodeSearch: "Searching", WebFetch: "Fetching",
  Task: "Delegating", Skill: "Using a skill",
};
const progress = { started: 0, tokens: 0, verb: "Considering", activity: "", detail: "", waiting: false, frame: 0 };
let progressTimer: number | undefined;

function startProgress(): void {
  Object.assign(progress, {
    started: Date.now(), tokens: 0, activity: "", detail: "", waiting: false, frame: 0,
    verb: VERBS[Math.floor(Math.random() * VERBS.length)],
  });
  progressLine.hidden = false;
  renderProgress();
  if (progressTimer === undefined) progressTimer = window.setInterval(tickProgress, 120);
}

function stopProgress(): void {
  progressLine.hidden = true;
  if (progressTimer !== undefined) window.clearInterval(progressTimer);
  progressTimer = undefined;
}

/** What it's doing now ("" = the turn's word), and a detail such as the tool call. */
function setActivity(activity = "", detail = ""): void {
  progress.activity = activity;
  progress.detail = detail;
  progress.waiting = false;
  if (busy) renderProgress();
}

function tickProgress(): void {
  progress.frame = (progress.frame + 1) % SPARKS.length;
  renderProgress();
}

function renderProgress(): void {
  const spark = progressLine.querySelector<HTMLElement>(".spark")!;
  spark.textContent = progress.waiting ? "✻" : SPARKS[progress.frame];
  progressLine.classList.toggle("waiting", progress.waiting);
  const verb = progress.waiting ? "Waiting for your answer" : progress.activity || progress.verb;
  progressLine.querySelector(".verb")!.textContent = `${verb}…`;
  progressLine.querySelector(".detail")!.textContent = progress.detail && !progress.waiting ? progress.detail : "";
  const parts = [duration(Date.now() - progress.started)];
  if (progress.tokens) parts.push(`↓ ${shortCount(progress.tokens)} tokens`);
  parts.push("Esc to interrupt");
  progressLine.querySelector(".meta")!.textContent = `(${parts.join(" · ")})`;
}

// --- messages typed while it works: sent together when the turn ends ---------

const queue: { text: string; includeContext: boolean }[] = [];

function renderQueue(): void {
  queuedBox.hidden = queue.length === 0;
  queuedBox.replaceChildren(
    ...queue.map((q, i) => {
      const row = el("div", "item");
      row.append(el("span", "text", `↳ ${q.text}`));
      const remove = el("button", "secondary remove", "×");
      remove.title = "Don't send this";
      remove.onclick = () => {
        queue.splice(i, 1);
        renderQueue();
      };
      row.append(remove);
      return row;
    }),
  );
}

/** The turn ended: send what was queued, or (interrupted) give it back to edit. */
function flushQueue(interrupted: boolean): void {
  if (!queue.length) return;
  const items = queue.splice(0);
  renderQueue();
  const text = items.map((q) => q.text).join("\n\n");
  if (interrupted) {
    input.value = text + (input.value ? `\n\n${input.value}` : "");
    input.focus();
    return;
  }
  submit(text, items.some((q) => q.includeContext));
}

function showStatus(text?: string): void {
  statusLine.hidden = !text;
  statusLine.textContent = text ?? "";
}

// --- rendering helpers ---------------------------------------------------------

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function append(node: HTMLElement): HTMLElement {
  const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 40;
  log.append(node);
  if (atBottom) node.scrollIntoView({ block: "end" });
  return node;
}

function note(text: string, cls: string): void {
  append(el("div", `note ${cls}`, text));
}

function renderReply(): void {
  renderQueued = false;
  if (reply) {
    setMarkdown(reply.el, reply.text);
    log.scrollTop = log.scrollHeight;
  }
}

function queueRender(): void {
  if (!renderQueued) {
    renderQueued = true;
    requestAnimationFrame(renderReply);
  }
}

function clip(text: string, maxLines: number): string {
  const lines = text.replace(/\s+$/, "").split("\n");
  return lines.length > maxLines
    ? lines.slice(0, maxLines).join("\n") + `\n… ${lines.length - maxLines} more lines`
    : lines.join("\n");
}

// --- events from cmcoder ---------------------------------------------------------

// A subagent's steps, listed inside its Task call's card.
const subagentSteps = new Map<string, HTMLElement>();

function subagentStep(ev: ToolUse | ToolResult | PermissionDenied, parentId: string): void {
  const card = toolCards.get(parentId);
  if (ev.type === "tool_use") {
    if (!card) return;
    let steps = card.querySelector<HTMLDetailsElement>("details.steps");
    if (!steps) {
      steps = card.appendChild(document.createElement("details"));
      steps.className = "steps";
      steps.open = true; // folded when the subagent finishes
      steps.append(el("summary", "", "Subagent steps"));
    }
    subagentSteps.set(ev.id, steps.appendChild(el("div", "step", `● ${ev.label}`)));
    setActivity("Subagents working");
    return;
  }
  const row = subagentSteps.get(ev.id);
  subagentSteps.delete(ev.id);
  if (ev.type === "permission_denied") {
    deniedHere.delete(ev.id);
    row?.append(el("span", "error", " — denied"));
  } else if (ev.is_error) {
    row?.append(el("span", "error", ` — ${ev.content.split("\n")[0].slice(0, 200)}`));
  }
}

// --- the agent map: this turn's subagents, above the input ----------------------

const STATE_MARKS: Record<string, string> = {
  queued: "○", running: "◐", waiting: "⏸", stopping: "◑",
  done: "✓", limit: "✓", stopped: "■", failed: "✗",
};
const STATE_WORDS: Record<string, string> = {
  queued: "queued", running: "running", waiting: "waiting for permission",
  stopping: "stopping (writing its report)", done: "done", limit: "done (step limit reached)",
  stopped: "stopped", failed: "failed",
};
const ACTIVE = new Set(["queued", "running", "waiting", "stopping"]);
const TICKING = new Set(["running", "waiting", "stopping"]);
const agentRuns = new Map<string, { status: SubagentStatus; seen: number }>();
let agentTimer: number | undefined;

function shortCount(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : `${n}`;
}

function duration(ms: number): string {
  const s = Math.floor(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function agentSummary(s: SubagentStatus, live: boolean): string {
  const entry = agentRuns.get(s.id);
  const ms = live && entry && TICKING.has(s.state) ? s.elapsed_ms + (Date.now() - entry.seen) : s.elapsed_ms;
  const tools = `${s.tool_uses} tool${s.tool_uses === 1 ? "" : "s"}`;
  return [s.agent_type, `${s.steps}/${s.max_steps} steps`, tools, `${shortCount(s.tokens)} tokens`, duration(ms)].join(" · ");
}

function agentDetail(s: SubagentStatus): string {
  if (s.state === "queued") return `${s.agent_type} · queued`;
  const words = s.state === "running" || s.state === "done" ? "" : `${STATE_WORDS[s.state]} · `;
  return words + agentSummary(s, true);
}

function subagentStatus(s: SubagentStatus): void {
  agentRuns.set(s.id, { status: s, seen: Date.now() });
  const card = toolCards.get(s.id) ?? document.querySelector<HTMLElement>(`.tool[data-task="${CSS.escape(s.id)}"]`);
  if (card) {
    card.dataset.task = s.id;
    let line = card.querySelector<HTMLElement>(".substatus");
    if (!line) line = card.insertBefore(el("div", "substatus"), card.children[1] ?? null);
    line.className = `substatus ${s.state}`;
    line.textContent = `${STATE_MARKS[s.state]} ${STATE_WORDS[s.state]} · ${agentSummary(s, false)}`;
  }
  renderAgents();
  if (agentTimer === undefined) agentTimer = window.setInterval(renderAgents, 1000);
}

function renderAgents(): void {
  const runs = Array.from(agentRuns.values()).map((r) => r.status);
  const active = runs.filter((s) => ACTIVE.has(s.state));
  agentsPanel.hidden = runs.length === 0;
  if (!active.length && agentTimer !== undefined) {
    window.clearInterval(agentTimer);
    agentTimer = undefined;
  }
  if (!runs.length) return;
  const done = runs.length - active.length;
  const title = el("div", "title", `Subagents · ${active.length} active · ${done} finished `);
  const map = el("button", "secondary open-map", "Map");
  map.title = "Open the Agent Navigator: this turn's agents as a mind map";
  map.onclick = () => post({ kind: "openNavigator" });
  title.append(map);
  const rows = runs.map((s, i) => {
    const row = el("div", `agent ${s.state}`);
    row.title = "Show its card";
    row.onclick = () => document.querySelector(`.tool[data-task="${CSS.escape(s.id)}"]`)?.scrollIntoView({ block: "center" });
    const branch = i === runs.length - 1 ? "└─" : "├─";
    row.append(el("span", "name", `${branch} ${STATE_MARKS[s.state]} ${s.number}. ${s.description}`));
    row.append(el("span", "detail", agentDetail(s)));
    if (ACTIVE.has(s.state) && s.state !== "stopping") {
      const stop = el("button", "secondary stop-one", "Stop");
      stop.title = "Stop this subagent: it reports what it has found so far; the others go on";
      stop.onclick = (e) => {
        e.stopPropagation();
        stop.setAttribute("disabled", "");
        post({ kind: "stopSubagent", id: s.id });
      };
      row.append(stop);
    }
    const parts = [row];
    if (s.activity && TICKING.has(s.state)) parts.push(el("div", "activity", `└ ${s.activity}`));
    return parts;
  });
  agentsPanel.replaceChildren(title, ...rows.flat());
}

function endAgents(): void {
  // The turn ended: anything still running was stopped with it.
  for (const entry of agentRuns.values()) {
    if (ACTIVE.has(entry.status.state)) subagentStatus({ ...entry.status, state: "stopped", activity: "" });
  }
  agentRuns.clear();
  renderAgents();
}

function onEvent(ev: AgentEvent): void {
  if ((ev.type === "tool_use" || ev.type === "tool_result" || ev.type === "permission_denied") && ev.parent_tool_use_id) {
    subagentStep(ev, ev.parent_tool_use_id);
    return;
  }
  switch (ev.type) {
    case "system_init":
      modelLabel.textContent = ev.model;
      modelLabel.title = `${ev.model} (${ev.provider}) · ${ev.cwd}`;
      modeSelect.value = ev.permission_mode;
      break;
    case "assistant_delta":
      if (progress.activity || progress.detail) setActivity();
      if (!reply) reply = { el: append(el("div", "msg assistant")), text: "" };
      reply.text += ev.text;
      queueRender();
      break;
    case "reasoning_delta":
      if (progress.activity !== "Thinking") setActivity("Thinking");
      break;
    case "assistant_message":
      if (ev.text && !reply) reply = { el: append(el("div", "msg assistant")), text: "" };
      if (reply) {
        reply.text = ev.text || reply.text;
        renderReply();
      }
      reply = undefined;
      if (ev.tool_calls.length) setActivity();
      break;
    case "tool_use":
      if (ev.name !== "TodoWrite") toolCard(ev);
      if (ev.input.subagent_type === "critic") setActivity("Reviewing the answer");
      else setActivity(TOOL_VERBS[ev.name] ?? "Working", ev.name === "TodoWrite" ? "" : ev.label);
      break;
    case "tool_result":
      toolResult(ev);
      setActivity();
      break;
    case "subagent_status":
      subagentStatus(ev);
      break;
    case "permission_request":
      permissionCard(ev);
      break;
    case "permission_denied":
      if (!deniedHere.delete(ev.id)) note(`Denied: ${ev.reason}`, "warn");
      break;
    case "todo_update":
      renderTodos(ev.todos);
      break;
    case "usage": {
      const k = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : `${n}`);
      usageLabel.textContent = ev.context_window
        ? `${k(ev.prompt_tokens)} / ${k(ev.context_window)} tokens`
        : `${k(ev.prompt_tokens)} tokens`;
      progress.tokens += ev.completion_tokens;
      if (busy) renderProgress();
      break;
    }
    case "warning":
      note(`⚠ ${ev.message}`, "warn");
      break;
    case "error":
      note(`✗ ${ev.message}${ev.hint ? `\n${ev.hint}` : ""}`, "error");
      break;
    case "review_result":
      reviewNote(ev);
      if (!ev.final) setActivity("Fixing what the reviewer found");
      break;
    case "code_context": {
      const where = ev.items.slice(0, 4).map((i) => `${i.path}:${i.start_line}-${i.end_line}`).join(", ");
      const more = ev.items.length > 4 ? ` and ${ev.items.length - 4} more` : "";
      note(`◦ Added code from the index: ${where}${more} (≈${ev.tokens} tokens)`, "info");
      break;
    }
    case "compacted":
      note(
        `✻ ${ev.trigger === "auto" ? "Context nearly full: compacted" : "Compacted"} the conversation ` +
          `(summarised ${ev.summarized_messages} earlier messages with ${ev.model}).`,
        "info",
      );
      break;
    case "mode_changed":
      modeSelect.value = ev.mode;
      break;
    case "history":
      for (const item of ev.messages) {
        if (item.role === "user") append(el("div", "msg user", item.text));
        else if (item.role === "tool") append(el("div", "tool")).append(el("div", "label", `● ${item.text}`));
        else setMarkdown(append(el("div", "msg assistant")), item.text);
      }
      if (rewound) note(rewound, "info");
      else if (ev.messages.length) note("Resumed this conversation.", "info");
      rewound = undefined;
      break;
    case "rewind_points":
      note("Choose the message to go back to in the list at the top of the window.", "info");
      break;
    case "rewound": {
      const restored = ev.actions.filter((a) => a.action === "restored").length;
      const deleted = ev.actions.filter((a) => a.action.startsWith("deleted")).length;
      const files = ev.code ? ` Files: ${restored} restored, ${deleted} deleted (changes made by Bash commands are not undone).` : "";
      if (ev.conversation) {
        log.replaceChildren(); // the history that follows redraws it
        toolCards.clear();
        permissionCards.clear();
        rewound = `Rewound the conversation.${files}`;
        if (ev.prompt) {
          input.value = ev.prompt;
          input.focus();
        }
      } else {
        note(`Rewound the code.${files}`, "info");
      }
      break;
    }
    case "session_list":
      renderSessions(ev.sessions);
      break;
    case "command_list":
      commands = ev.commands;
      updateCommands();
      break;
    case "model_changed":
      modelLabel.textContent = ev.model;
      break;
    case "result":
      if (reply) renderReply();
      reply = undefined;
      for (const card of permissionCards.values()) answered(card, "Cancelled");
      permissionCards.clear();
      endAgents();
      if (ev.subtype === "interrupted") note(`Interrupted. What should ${PRODUCT} do instead?`, "warn");
      else if (ev.subtype === "max_turns") note(`Stopped: ${ev.result}`, "warn");
      setBusy(false);
      input.focus();
      flushQueue(ev.subtype === "interrupted");
      break;
  }
}

/** Critique: what the critic decided about the answer. */
function reviewNote(ev: ReviewResult): void {
  const n = ev.issues.length;
  const problems = `${n} problem${n === 1 ? "" : "s"}`;
  let head: string;
  let cls = "warn";
  if (!ev.final) head = `↺ The reviewer found ${problems}; fixing ${n === 1 ? "it" : "them"} (review ${ev.round}/${ev.max_rounds}):`;
  else if (ev.verdict === "pass") {
    head = `✓ Reviewed: ${ev.summary}`;
    cls = "info review-pass";
  } else if (ev.verdict === "fail") head = `⚠ Not validated: after ${ev.round} review${ev.round === 1 ? "" : "s"} the reviewer still found ${problems}:`;
  else head = `⚠ Not reviewed: ${ev.summary}`;
  const box = append(el("div", `note review ${cls}`));
  box.append(el("div", "head", head));
  if (ev.verdict !== "pass" && ev.verdict !== "none") {
    const list = el("ul", "issues");
    for (const i of ev.issues) {
      const item = el("li", `issue ${i.severity}`, `[${i.severity}] ${i.problem}${i.where ? ` (${i.where})` : ""}`);
      if (i.fix) item.append(el("div", "fix", `fix: ${i.fix}`));
      list.append(item);
    }
    if (!n && ev.summary) list.append(el("li", "issue", ev.summary));
    box.append(list);
  }
}

function toolCard(ev: ToolUse): void {
  const card = el("div", "tool");
  card.append(el("div", "label", `● ${ev.label}`));
  toolCards.set(ev.id, append(card));
}

function toolResult(ev: ToolResult): void {
  if (ev.name === "TodoWrite") return;
  const card = toolCards.get(ev.id) ?? append(el("div", "tool"));
  toolCards.delete(ev.id);
  const steps = card.querySelector<HTMLDetailsElement>("details.steps");
  if (steps) {
    // A finished subagent: fold its steps away (a click shows them again).
    const n = steps.querySelectorAll(".step").length;
    steps.open = false;
    steps.querySelector("summary")!.textContent = `${n} subagent step${n === 1 ? "" : "s"}`;
  }
  if (ev.is_error) {
    card.append(el("pre", "result error", clip(ev.content, 4)));
    return;
  }
  card.append(el("div", "result", `└ ${ev.summary || "done"}`));
  if (ev.name === "Bash" && ev.content.trim() && ev.content.trim() !== "(no output)") {
    card.append(el("pre", "output", clip(ev.content, 3)));
  }
}

function permissionCard(ev: PermissionRequest): void {
  progress.waiting = true;
  if (busy) renderProgress();
  const card = el("div", "permission");
  card.dataset.toolUseId = ev.tool_use_id;
  card.append(el("div", "title", `Allow ${ev.label}?`));
  if (ev.reason) card.append(el("div", "reason", ev.reason));
  const preview = permissionPreview(ev);
  if (preview) card.append(el("pre", "preview", preview));
  if (ev.change) {
    const show = el("button", "link", "Show diff");
    show.title = "Open the proposed change in VS Code's diff editor";
    show.onclick = () => post({ kind: "showDiff", requestId: ev.request_id });
    card.append(show);
  }

  const feedback = document.createElement("input");
  feedback.type = "text";
  feedback.placeholder = `Optional: tell ${PRODUCT} what to do instead (when denying)`;

  const buttons = el("div", "buttons");
  const answer = (allow: boolean, remember: boolean) => {
    post({ kind: "permission", requestId: ev.request_id, allow, remember, feedback: allow ? undefined : feedback.value.trim() });
    permissionCards.delete(ev.request_id);
    if (!allow) deniedHere.add(ev.tool_use_id);
    answered(card, allow ? (remember ? "Allowed (always)" : "Allowed") : "Denied");
  };
  const allow = el("button", "", "Allow");
  allow.onclick = () => answer(true, false);
  buttons.append(allow);
  if (ev.can_remember) {
    const always = el("button", "secondary", "Always allow");
    always.title = `Saves the rule ${ev.suggested_rule} for this project`;
    always.onclick = () => answer(true, true);
    buttons.append(always);
  }
  const deny = el("button", "secondary", "Deny");
  deny.onclick = () => answer(false, false);
  buttons.append(deny);
  feedback.onkeydown = (e) => {
    if (e.key === "Enter") answer(false, false);
  };
  card.append(buttons, feedback);
  permissionCards.set(ev.request_id, append(card));
  allow.focus();
}

function permissionPreview(ev: PermissionRequest): string {
  const i = ev.input as Record<string, unknown>;
  const s = (v: unknown) => (typeof v === "string" ? v : "");
  switch (ev.name) {
    case "Bash":
      return s(i.command);
    case "Write":
      return `${s(i.file_path)}\n\n${clip(s(i.content), 30)}`;
    case "Edit": {
      const lines = (prefix: string, text: string) =>
        clip(text, 15).split("\n").map((l) => prefix + l).join("\n");
      return `${s(i.file_path)}\n\n${lines("- ", s(i.old_string))}\n${lines("+ ", s(i.new_string))}`;
    }
    default:
      return clip(JSON.stringify(i, null, 2), 20);
  }
}

function answered(card: HTMLElement, text: string): void {
  progress.waiting = false;
  card.querySelectorAll("button, input").forEach((e) => e.remove());
  card.classList.add("answered");
  card.append(el("div", "answer", `└ ${text}`));
}

function renderTodos(todos: Record<string, unknown>[]): void {
  todosPanel.replaceChildren();
  todosPanel.hidden = todos.length === 0;
  if (!todos.length) return;
  const done = todos.filter((t) => t.status === "completed").length;
  todosPanel.append(el("div", "title", `Todo list · ${done}/${todos.length} done`));
  for (const t of todos) {
    const status = String(t.status);
    todosPanel.append(el("div", `todo ${status}`, `${MARKS[status] ?? "☐"} ${String(t.content ?? "")}`));
  }
}

// --- input ---------------------------------------------------------------------

function send(): void {
  const text = input.value.trim();
  if (!text || !ready) return;
  const includeContext = !contextChip.hidden && contextBox.checked;
  input.value = "";
  commandsPopup.hidden = true;
  contextBox.checked = true; // turning it off counts for one message
  if (busy) {
    queue.push({ text, includeContext }); // sent when this turn ends
    renderQueue();
    return;
  }
  submit(text, includeContext);
}

function submit(text: string, includeContext: boolean): void {
  const bubble = append(el("div", "msg user", text));
  if (includeContext) bubble.append(el("div", "attached", `📎 ${contextText.textContent}`));
  setBusy(true);
  post({ kind: "send", text, includeContext });
}

function renderSessions(sessions: { id: string; title: string; updated: number; messages: number }[]): void {
  sessionsPanel.replaceChildren(el("div", "title", "Past conversations"));
  if (!sessions.length) sessionsPanel.append(el("div", "empty", "None yet in this project."));
  for (const s of sessions.slice(0, 50)) {
    const row = el("button", "session secondary");
    row.append(el("span", "name", s.title || "(untitled)"));
    row.append(el("span", "when", `${new Date(s.updated * 1000).toLocaleString()} · ${s.messages} messages`));
    row.onclick = () => {
      sessionsPanel.hidden = true;
      post({ kind: "resume", id: s.id });
    };
    sessionsPanel.append(row);
  }
  sessionsPanel.hidden = false;
}

// --- slash-command completion ---------------------------------------------------

let commands: CommandInfo[] = [];
let matches: CommandInfo[] = [];
let selected = 0;

/** The `/name` being typed (nothing after it yet), or undefined. */
function typedCommand(): string | undefined {
  const m = /^\/([^\s]*)$/.exec(input.value);
  return m ? m[1] : undefined;
}

function updateCommands(): void {
  const typed = typedCommand();
  matches = typed === undefined ? [] : commands.filter((c) => c.name.startsWith(typed)).slice(0, 12);
  selected = Math.min(selected, Math.max(matches.length - 1, 0));
  commandsPopup.replaceChildren(
    ...matches.map((c, i) => {
      const row = el("div", i === selected ? "command selected" : "command");
      row.setAttribute("role", "option");
      row.append(el("span", "name", `/${c.name}`));
      if (c.argument_hint) row.append(el("span", "hint", ` ${c.argument_hint}`));
      const where = c.origin === "project" ? " (project)" : c.origin === "mcp" ? " (MCP)" : "";
      row.append(el("span", "description", `${c.description}${where}`));
      row.onmousedown = (e) => {
        e.preventDefault();
        acceptCommand(i);
      };
      return row;
    }),
  );
  commandsPopup.hidden = matches.length === 0;
}

function acceptCommand(i: number): void {
  const c = matches[i];
  if (!c) return;
  input.value = `/${c.name} `;
  matches = [];
  commandsPopup.hidden = true;
  input.focus();
}

input.addEventListener("input", () => {
  if (input.value === "/") post({ kind: "listCommands" }); // fresh: files may have changed
  updateCommands();
});

input.addEventListener("keydown", (e) => {
  if (!commandsPopup.hidden && matches.length) {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      selected = (selected + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
      updateCommands();
      return;
    }
    if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey && !e.isComposing && matches[selected]?.name !== typedCommand())) {
      e.preventDefault();
      acceptCommand(selected);
      return;
    }
    if (e.key === "Escape") {
      e.stopPropagation();
      commandsPopup.hidden = true;
      return;
    }
  }
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    send();
  }
});
sendButton.onclick = send;
stopButton.onclick = () => post({ kind: "interrupt" });
attachButton.onclick = () => post({ kind: "attachFile" });
historyButton.onclick = () => {
  if (!sessionsPanel.hidden) sessionsPanel.hidden = true;
  else if (!busy) post({ kind: "listSessions" });
};
modeSelect.onchange = () => post({ kind: "setMode", mode: modeSelect.value });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && busy) post({ kind: "interrupt" });
});
// Links in replies open in the browser (only http/https), never inside the panel.
document.addEventListener("click", (e) => {
  const a = (e.target as HTMLElement).closest("a");
  if (a) {
    e.preventDefault();
    post({ kind: "openLink", href: a.getAttribute("href") ?? "" });
  }
});

// --- messages from the extension ----------------------------------------------

window.addEventListener("message", (e: MessageEvent<ToWebview>) => {
  const m = e.data;
  switch (m.kind) {
    case "event":
      onEvent(m.event);
      break;
    case "state":
      ready = m.state === "ready";
      if (m.state === "starting") showStatus(`Starting ${PRODUCT}…`);
      else if (m.state === "ready") showStatus();
      else {
        setBusy(false);
        showStatus();
        const box = el("div", "note error", m.message ?? `${PRODUCT} stopped.`);
        const restart = el("button", "", "Restart");
        restart.onclick = () => {
          restart.remove();
          post({ kind: "restart" });
        };
        box.append(document.createElement("br"), restart);
        append(box);
      }
      break;
    case "reset":
      sessionsPanel.hidden = true;
      log.replaceChildren();
      renderTodos([]);
      toolCards.clear();
      permissionCards.clear();
      agentRuns.clear();
      renderAgents();
      queue.splice(0);
      renderQueue();
      reply = undefined;
      usageLabel.textContent = "";
      setBusy(false);
      break;
    case "prefill":
      input.value = m.text + input.value;
      input.focus();
      break;
    case "context":
      contextChip.hidden = !m.label;
      contextText.textContent = m.label ?? "";
      break;
    case "permissionAnswered": {
      const card = permissionCards.get(m.requestId);
      if (card) {
        permissionCards.delete(m.requestId);
        if (m.text === "Denied" && card.dataset.toolUseId) deniedHere.add(card.dataset.toolUseId);
        answered(card, m.text);
      }
      break;
    }
  }
});

post({ kind: "ready" });
input.focus();
