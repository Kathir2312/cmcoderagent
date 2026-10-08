// Messages between the IDE side and the chat panel / Agent Navigator (the same in
// every IDE). The panel never talks to cmcoder directly; the IDE side relays.

import type { AgentEvent } from "./protocol";

export type AgentState = "starting" | "ready" | "exited";

/** An image the user pasted, dropped or picked: base64 without the data: prefix. */
export interface ImageAttachment {
  data: string;
  mediaType: string;
  name: string;
}

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
  | { kind: "permissionAnswered"; requestId: string; text: string }
  /** An image from the system clipboard, read by the IDE (answer to pasteImage). */
  | ({ kind: "image" } & ImageAttachment);

export type FromWebview =
  | { kind: "ready" }
  | { kind: "send"; text: string; includeContext: boolean; images?: ImageAttachment[] }
  /** A paste brought no image the page could read: ask the IDE for the clipboard's image. */
  | { kind: "pasteImage" }
  | { kind: "interrupt" }
  | { kind: "stopSubagent"; id: string }
  | { kind: "openNavigator" }
  | { kind: "permission"; requestId: string; allow: boolean; remember: boolean; feedback?: string }
  | { kind: "showDiff"; requestId: string }
  | { kind: "setMode"; mode: string }
  /** The Critique checkbox: on or off, saved for every cmcoder. */
  | { kind: "setCritique"; enabled: boolean }
  | { kind: "newConversation" }
  | { kind: "listSessions" }
  | { kind: "listCommands" }
  | { kind: "resume"; id: string }
  | { kind: "attachFile" }
  | { kind: "restart" }
  | { kind: "openLink"; href: string }
  /** The page's script failed: the IDE writes it to its log (the page shows it too). */
  | { kind: "panelError"; message: string }
  /** The page took cmcoder's state (so messages from the IDE reach it): for the IDE's log. */
  | { kind: "panelState"; state: AgentState };

// Messages between the extension and the Agent Navigator (an editor tab with
// the current turn's agents as a mind map).

export type ToNavigator =
  /** A new turn (or the tab just opened): start the map again. */
  | { kind: "reset"; prompt: string; model: string; busy: boolean }
  /** A protocol event of the current turn. */
  | { kind: "event"; event: AgentEvent };

export type FromNavigator =
  | { kind: "ready" }
  | { kind: "stopSubagent"; id: string }
  | { kind: "openChat" };
