// The chat panel (runs in the webview, a sandboxed browser page).
// Plain DOM code: it receives protocol events from the extension and renders them.

import { marked } from "marked";
import type { AgentEvent, PermissionRequest, ToolResult, ToolUse } from "../protocol";
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
  <div class="status" hidden></div>
  <footer>
    <label class="context" hidden title="Send the active file, selection and problems with this message">
      <input type="checkbox" checked> <span></span>
    </label>
    <textarea rows="3" placeholder="Ask cmcoder… (Enter to send, Shift+Enter for a new line)"></textarea>
    <div class="actions">
      <button class="attach secondary" title="Attach a file (@)">@</button>
      <span class="usage"></span>
      <button class="stop secondary" hidden title="Stop (Esc)">Stop</button>
      <button class="send">Send</button>
    </div>
  </footer>`;

const $ = <T extends HTMLElement>(sel: string) => app.querySelector(sel) as T;
const log = $<HTMLElement>(".log");
const input = $<HTMLTextAreaElement>("textarea");
const sendButton = $<HTMLButtonElement>(".send");
const stopButton = $<HTMLButtonElement>(".stop");
const modeSelect = $<HTMLSelectElement>(".mode");
const modelLabel = $<HTMLElement>(".model");
const usageLabel = $<HTMLElement>(".usage");
const statusLine = $<HTMLElement>(".status");
const todosPanel = $<HTMLElement>(".todos");
const sessionsPanel = $<HTMLElement>(".sessions");
const historyButton = $<HTMLButtonElement>(".history");
const attachButton = $<HTMLButtonElement>(".attach");
const contextChip = $<HTMLLabelElement>(".context");
const contextBox = contextChip.querySelector("input") as HTMLInputElement;
const contextText = contextChip.querySelector("span") as HTMLElement;

for (const mode of MODES) modeSelect.append(new Option(mode, mode));

// --- state -------------------------------------------------------------------

let busy = false;
let ready = false;
let reply: { el: HTMLElement; text: string } | undefined; // the streaming reply
let renderQueued = false;
const toolCards = new Map<string, HTMLElement>();
const permissionCards = new Map<string, HTMLElement>();
// Tool calls the user denied here: their card already says so.
const deniedHere = new Set<string>();

function setBusy(value: boolean): void {
  busy = value;
  sendButton.hidden = value;
  stopButton.hidden = !value;
  input.placeholder = value
    ? "cmcoder is working… (Esc to stop)"
    : "Ask cmcoder… (Enter to send, Shift+Enter for a new line)";
  if (!value) showStatus();
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

function onEvent(ev: AgentEvent): void {
  switch (ev.type) {
    case "system_init":
      modelLabel.textContent = ev.model;
      modelLabel.title = `${ev.model} (${ev.provider}) · ${ev.cwd}`;
      modeSelect.value = ev.permission_mode;
      break;
    case "assistant_delta":
      showStatus();
      if (!reply) reply = { el: append(el("div", "msg assistant")), text: "" };
      reply.text += ev.text;
      queueRender();
      break;
    case "reasoning_delta":
      showStatus("Thinking…");
      break;
    case "assistant_message":
      if (ev.text && !reply) reply = { el: append(el("div", "msg assistant")), text: "" };
      if (reply) {
        reply.text = ev.text || reply.text;
        renderReply();
      }
      reply = undefined;
      if (ev.tool_calls.length) showStatus("Working…");
      break;
    case "tool_use":
      if (ev.name !== "TodoWrite") toolCard(ev);
      break;
    case "tool_result":
      toolResult(ev);
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
      break;
    }
    case "warning":
      note(`⚠ ${ev.message}`, "warn");
      break;
    case "error":
      note(`✗ ${ev.message}${ev.hint ? `\n${ev.hint}` : ""}`, "error");
      break;
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
      if (ev.messages.length) note("Resumed this conversation.", "info");
      break;
    case "session_list":
      renderSessions(ev.sessions);
      break;
    case "model_changed":
      modelLabel.textContent = ev.model;
      break;
    case "result":
      if (reply) renderReply();
      reply = undefined;
      for (const card of permissionCards.values()) answered(card, "Cancelled");
      permissionCards.clear();
      if (ev.subtype === "interrupted") note("Interrupted. What should cmcoder do instead?", "warn");
      else if (ev.subtype === "max_turns") note(`Stopped: ${ev.result}`, "warn");
      setBusy(false);
      input.focus();
      break;
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
  showStatus();
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
  feedback.placeholder = "Optional: tell cmcoder what to do instead (when denying)";

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
  if (!text || busy || !ready) return;
  const includeContext = !contextChip.hidden && contextBox.checked;
  const bubble = append(el("div", "msg user", text));
  if (includeContext) bubble.append(el("div", "attached", `📎 ${contextText.textContent}`));
  input.value = "";
  contextBox.checked = true; // turning it off counts for one message
  setBusy(true);
  showStatus("Thinking…");
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

input.addEventListener("keydown", (e) => {
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
      if (m.state === "starting") showStatus("Starting cmcoder…");
      else if (m.state === "ready") showStatus();
      else {
        setBusy(false);
        showStatus();
        const box = el("div", "note error", m.message ?? "cmcoder stopped.");
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
