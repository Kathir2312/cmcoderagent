// Messages between the extension (Node) and the chat webview (browser).
// The webview never talks to cmcoder directly; the extension relays.

import type { AgentEvent } from "./protocol";

export type AgentState = "starting" | "ready" | "exited";

export type ToWebview =
  /** A protocol event from cmcoder. */
  | { kind: "event"; event: AgentEvent }
  | { kind: "state"; state: AgentState; message?: string }
  /** A new conversation: clear the panel. */
  | { kind: "reset" }
  /** Put text in the input box (e.g. from "Ask cmcoder about selection"). */
  | { kind: "prefill"; text: string }
  /** What the editor context would be for the next message (null: none). */
  | { kind: "context"; label: string | null }
  /** A permission request was answered outside the panel (e.g. in the diff editor). */
  | { kind: "permissionAnswered"; requestId: string; text: string };

export type FromWebview =
  | { kind: "ready" }
  | { kind: "send"; text: string; includeContext: boolean }
  | { kind: "interrupt" }
  | { kind: "permission"; requestId: string; allow: boolean; remember: boolean; feedback?: string }
  | { kind: "showDiff"; requestId: string }
  | { kind: "setMode"; mode: string }
  | { kind: "newConversation" }
  | { kind: "listSessions" }
  | { kind: "resume"; id: string }
  | { kind: "attachFile" }
  | { kind: "restart" }
  | { kind: "openLink"; href: string };
