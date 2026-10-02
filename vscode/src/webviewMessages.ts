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
  | { kind: "prefill"; text: string };

export type FromWebview =
  | { kind: "ready" }
  | { kind: "send"; text: string }
  | { kind: "interrupt" }
  | { kind: "permission"; requestId: string; allow: boolean; remember: boolean; feedback?: string }
  | { kind: "setMode"; mode: string }
  | { kind: "newConversation" }
  | { kind: "restart" }
  | { kind: "openLink"; href: string };
