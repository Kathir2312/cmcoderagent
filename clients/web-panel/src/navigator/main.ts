// The Agent Navigator (runs in its own webview, an editor tab): the current
// turn's agents as a live mind map. The main agent on the left, its subagents
// as branches, each subagent's last few tool calls as leaves. Click a node for
// its steps and report; Stop a running subagent from its node.

import type { AgentEvent, SubagentStatus } from "../protocol";
import { bridge, fromHost, hostSettings } from "../bridge";
import type { FromNavigator, ToNavigator } from "../messages";

const host = bridge<FromNavigator>();
const post = (m: FromNavigator) => host.post(m);
hostSettings();

// --- layout (pixels) -------------------------------------------------------------

const PAD = 24;
const ROOT_W = 250;
const ROOT_H = 92;
const AGENT_W = 320;
const AGENT_H = 72;
const LEAF_W = 290;
const LEAF_H = 26;
const COL_GAP = 64;
const BLOCK_GAP = 18;
const LEAF_GAP = 6;
const LEAVES = 3; // tool calls shown per subagent; the rest fold into "…N more"

const MARKS: Record<string, string> = {
  queued: "○", running: "◐", waiting: "⏸", stopping: "◑",
  done: "✓", limit: "✓", stopped: "■", failed: "✗",
};
const WORDS: Record<string, string> = {
  queued: "queued", running: "running", waiting: "waiting for permission",
  stopping: "stopping (writing its report)", done: "done", limit: "done (step limit reached)",
  stopped: "stopped", failed: "failed",
};
const ACTIVE = new Set(["queued", "running", "waiting", "stopping"]);
const TICKING = new Set(["running", "waiting", "stopping"]);

// --- state ---------------------------------------------------------------------

interface Step {
  id: string;
  label: string;
  problem?: string; // an error or a denial
}

interface Agent {
  id: string; // the Task call's id
  label: string;
  description: string;
  status?: SubagentStatus;
  seen: number; // when the status arrived (the time ticks from there)
  steps: Step[];
  report?: string;
  reportError?: boolean;
}

const state = {
  prompt: "",
  model: "",
  busy: false,
  outcome: "",
  mainTools: 0, // the main agent's own tool calls this turn
  agents: new Map<string, Agent>(),
  selected: undefined as string | undefined,
  zoom: 1,
};

// --- page ------------------------------------------------------------------------

const app = document.getElementById("app")!;
app.innerHTML = `
  <header>
    <span class="title">Agent Navigator</span>
    <span class="summary"></span>
    <span class="spacer"></span>
    <button class="zoom-out secondary" title="Smaller">−</button>
    <span class="zoom-level">100%</span>
    <button class="zoom-in secondary" title="Larger">+</button>
    <button class="chat secondary" title="Show the chat">Chat</button>
  </header>
  <div class="body">
    <main class="viewport"><div class="canvas"></div></main>
    <aside class="details" hidden></aside>
  </div>`;
const $ = <T extends HTMLElement>(sel: string) => app.querySelector(sel) as T;
const canvas = $<HTMLElement>(".canvas");
const details = $<HTMLElement>(".details");
const summary = $<HTMLElement>(".summary");
const zoomLevel = $<HTMLElement>(".zoom-level");
$<HTMLButtonElement>(".chat").onclick = () => post({ kind: "openChat" });
$<HTMLButtonElement>(".zoom-in").onclick = () => setZoom(state.zoom + 0.1);
$<HTMLButtonElement>(".zoom-out").onclick = () => setZoom(state.zoom - 0.1);

function setZoom(z: number): void {
  state.zoom = Math.min(1.6, Math.max(0.5, Math.round(z * 10) / 10));
  zoomLevel.textContent = `${Math.round(state.zoom * 100)}%`;
  canvas.style.transform = `scale(${state.zoom})`;
}

function el(tag: string, cls: string, text?: string): HTMLElement {
  const e = document.createElement(tag);
  e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function shortCount(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k` : `${n}`;
}

function duration(ms: number): string {
  const s = Math.floor(ms / 1000);
  return s >= 60 ? `${Math.floor(s / 60)}m${String(s % 60).padStart(2, "0")}s` : `${s}s`;
}

function stateOf(a: Agent): string {
  return a.status?.state ?? "running";
}

/** Its numbers on two lines: "explore · 14/100 steps", "22 tools · 31.4k tokens · 1m12s". */
function statLines(a: Agent): [string, string] {
  const s = a.status;
  if (!s) return ["starting…", ""];
  if (s.state === "queued") return [`${s.agent_type} · queued`, ""];
  const ms = TICKING.has(s.state) ? s.elapsed_ms + (Date.now() - a.seen) : s.elapsed_ms;
  const tools = `${s.tool_uses} tool${s.tool_uses === 1 ? "" : "s"}`;
  return [`${s.agent_type} · ${s.steps}/${s.max_steps} steps`, [tools, `${shortCount(s.tokens)} tokens`, duration(ms)].join(" · ")];
}

function stats(a: Agent): string {
  return statLines(a).filter(Boolean).join(" · ");
}

// --- events -----------------------------------------------------------------------

function agentFor(id: string, label = "Task"): Agent {
  let a = state.agents.get(id);
  if (!a) {
    a = { id, label, description: label, seen: Date.now(), steps: [] };
    state.agents.set(id, a);
  }
  return a;
}

function onEvent(ev: AgentEvent): void {
  switch (ev.type) {
    case "tool_use":
      if (ev.parent_tool_use_id) {
        agentFor(ev.parent_tool_use_id).steps.push({ id: ev.id, label: ev.label });
      } else if (ev.name === "Task") {
        const a = agentFor(ev.id, ev.label);
        a.label = ev.label;
        a.description = String(ev.input.description ?? ev.label);
      } else {
        state.mainTools += 1;
      }
      break;
    case "tool_result":
      if (ev.parent_tool_use_id) {
        const step = state.agents.get(ev.parent_tool_use_id)?.steps.find((s) => s.id === ev.id);
        if (step && ev.is_error) step.problem = ev.content.split("\n")[0].slice(0, 200);
      } else if (state.agents.has(ev.id)) {
        const a = state.agents.get(ev.id)!;
        a.report = ev.content;
        a.reportError = ev.is_error;
      }
      break;
    case "permission_denied":
      if (ev.parent_tool_use_id) {
        const step = state.agents.get(ev.parent_tool_use_id)?.steps.find((s) => s.id === ev.id);
        if (step) step.problem = `denied: ${ev.reason}`;
      }
      break;
    case "subagent_status": {
      const a = agentFor(ev.id, ev.description);
      a.description = ev.description;
      a.status = ev;
      a.seen = Date.now();
      break;
    }
    case "result":
      state.busy = false;
      state.outcome = ev.subtype;
      for (const a of state.agents.values()) {
        if (a.status && ACTIVE.has(a.status.state)) a.status = { ...a.status, state: "stopped", activity: "" };
      }
      break;
    default:
      return;
  }
  render();
}

// --- drawing ------------------------------------------------------------------------

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

function place(node: HTMLElement, box: Box): HTMLElement {
  Object.assign(node.style, { left: `${box.x}px`, top: `${box.y}px`, width: `${box.w}px`, height: `${box.h}px` });
  canvas.append(node);
  return node;
}

function link(svg: SVGSVGElement, from: Box, to: Box, cls: string): void {
  const x1 = from.x + from.w;
  const y1 = from.y + from.h / 2;
  const x2 = to.x;
  const y2 = to.y + to.h / 2;
  const dx = (x2 - x1) / 2;
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`);
  path.setAttribute("class", `link ${cls}`);
  svg.append(path);
}

function render(): void {
  const agents = Array.from(state.agents.values());
  const active = agents.filter((a) => ACTIVE.has(stateOf(a))).length;
  summary.textContent = agents.length
    ? `this turn · ${agents.length} subagent${agents.length === 1 ? "" : "s"} · ${active} active`
    : "this turn";
  canvas.replaceChildren();
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  canvas.append(svg);

  // Each subagent's block: its node, and its leaves beside it.
  const agentX = PAD + ROOT_W + COL_GAP;
  const leafX = agentX + AGENT_W + COL_GAP;
  let y = PAD;
  const blocks = agents.map((a) => {
    const shown = a.steps.slice(-LEAVES);
    const more = a.steps.length - shown.length;
    const leaves = shown.length + (more ? 1 : 0);
    const leavesH = leaves ? leaves * (LEAF_H + LEAF_GAP) - LEAF_GAP : 0;
    const h = Math.max(AGENT_H, leavesH);
    const block = { a, shown, more, top: y, h, leavesH };
    y += h + BLOCK_GAP;
    return block;
  });
  const contentH = Math.max(ROOT_H, y - BLOCK_GAP - PAD);
  const root: Box = { x: PAD, y: PAD + (contentH - ROOT_H) / 2, w: ROOT_W, h: ROOT_H };
  const width = (agents.some((a) => a.steps.length) ? leafX + LEAF_W : agentX + AGENT_W) + PAD;
  canvas.style.width = `${Math.max(width, PAD * 2 + ROOT_W)}px`;
  canvas.style.height = `${contentH + PAD * 2}px`;
  svg.setAttribute("width", canvas.style.width);
  svg.setAttribute("height", canvas.style.height);

  // The main agent.
  const main = el("div", `node root ${state.busy ? "working" : state.outcome || "idle"}`);
  main.append(el("div", "line1", "● main agent"));
  main.append(el("div", "line3", state.model));
  const prompt = el("div", "line2 prompt", state.prompt ? `“${state.prompt}”` : "No turn yet: send a message in the chat.");
  prompt.title = state.prompt;
  main.append(prompt);
  const how = state.busy ? "working…" : state.outcome === "interrupted" ? "interrupted" : state.outcome ? "done" : "";
  const own = state.mainTools ? `${state.mainTools} tool call${state.mainTools === 1 ? "" : "s"} of its own` : "";
  main.append(el("div", "line3", [how, own].filter(Boolean).join(" · ")));
  place(main, root);

  if (!agents.length) {
    const empty = el("div", "empty", "No subagents in this turn yet. When cmcoder hands work to subagents (\"one subagent per project\"), they appear here as branches.");
    place(empty, { x: agentX, y: root.y, w: AGENT_W + COL_GAP + LEAF_W, h: ROOT_H });
  }

  for (const b of blocks) {
    const s = stateOf(b.a);
    const box: Box = { x: agentX, y: b.top + (b.h - AGENT_H) / 2, w: AGENT_W, h: AGENT_H };
    link(svg, root, box, s);
    const node = el("div", `node agent ${s}${state.selected === b.a.id ? " selected" : ""}`);
    node.dataset.id = b.a.id;
    node.title = "Show its steps and report";
    const n = b.a.status ? `${b.a.status.number}. ` : "";
    node.append(el("div", "line1", `${MARKS[s] ?? "◐"} ${n}${b.a.description}`));
    const [first, second] = statLines(b.a);
    const words = s === "running" || s === "done" || s === "queued" ? "" : `${WORDS[s]} · `;
    node.append(el("div", "line2", words + first));
    node.append(el("div", "line3", second));
    if (ACTIVE.has(s) && s !== "stopping") {
      const stop = el("button", "stop secondary", "Stop");
      stop.title = "Stop this subagent: it reports what it has found so far; the others go on";
      stop.onclick = (e) => {
        e.stopPropagation();
        stop.setAttribute("disabled", "");
        post({ kind: "stopSubagent", id: b.a.id });
      };
      node.append(stop);
    }
    node.onclick = () => select(b.a.id);
    place(node, box);

    // Leaves: the last few tool calls, the newest at the bottom.
    let ly = b.top + (b.h - b.leavesH) / 2;
    const leaf = (text: string, cls: string): void => {
      const lb: Box = { x: leafX, y: ly, w: LEAF_W, h: LEAF_H };
      link(svg, box, lb, `${s} thin`);
      const node = el("div", `node leaf ${cls}`, text);
      node.title = text;
      node.onclick = () => select(b.a.id);
      place(node, lb);
      ly += LEAF_H + LEAF_GAP;
    };
    if (b.more) leaf(`… ${b.more} earlier step${b.more === 1 ? "" : "s"}`, "more");
    b.shown.forEach((step, i) => {
      const now = i === b.shown.length - 1 && TICKING.has(s) && b.a.status?.activity === step.label;
      leaf(step.problem ? `${step.label} — ${step.problem}` : step.label, step.problem ? "problem" : now ? `current ${s}` : "");
    });
  }
  renderDetails();
}

function select(id: string): void {
  state.selected = state.selected === id ? undefined : id;
  render();
}

function renderDetails(): void {
  const a = state.selected ? state.agents.get(state.selected) : undefined;
  details.hidden = !a;
  if (!a) return;
  const s = stateOf(a);
  const close = el("button", "close secondary", "×");
  close.title = "Close";
  close.onclick = () => select(a.id);
  const head = el("div", "head");
  head.append(el("div", `name ${s}`, `${MARKS[s] ?? "◐"} ${a.status ? `${a.status.number}. ` : ""}${a.description}`), close);
  const parts: HTMLElement[] = [head, el("div", "state", `${WORDS[s] ?? s} · ${stats(a)}`)];
  if (a.status?.model) parts.push(el("div", "model", `model ${a.status.model}`));
  if (a.status?.activity && TICKING.has(s)) parts.push(el("div", "now", `now: ${a.status.activity}`));
  if (ACTIVE.has(s) && s !== "stopping") {
    const stop = el("button", "stop", "Stop this subagent");
    stop.onclick = () => {
      stop.setAttribute("disabled", "");
      post({ kind: "stopSubagent", id: a.id });
    };
    parts.push(stop);
  }
  parts.push(el("div", "section", `Steps (${a.steps.length})`));
  const list = el("ol", "steps");
  for (const step of a.steps) {
    const item = el("li", step.problem ? "problem" : "", step.label);
    if (step.problem) item.append(el("div", "why", step.problem));
    list.append(item);
  }
  if (!a.steps.length) list.append(el("li", "none", "none yet"));
  parts.push(list);
  if (a.report !== undefined) {
    parts.push(el("div", "section", "Report"));
    parts.push(el("pre", `report${a.reportError ? " problem" : ""}`, a.report));
  }
  details.replaceChildren(...parts);
}

// Times tick while anything runs.
window.setInterval(() => {
  if (Array.from(state.agents.values()).some((a) => TICKING.has(stateOf(a)))) render();
}, 1000);

// --- messages from the extension -------------------------------------------------

window.addEventListener("message", (e: MessageEvent<ToNavigator>) => {
  if (!fromHost(e)) return;
  const m = e.data;
  if (m.kind === "reset") {
    Object.assign(state, { prompt: m.prompt, model: m.model, busy: m.busy, outcome: "", mainTools: 0, selected: undefined });
    state.agents.clear();
    render();
  } else if (m.kind === "event") {
    onEvent(m.event);
  }
});

render();
post({ kind: "ready" });
