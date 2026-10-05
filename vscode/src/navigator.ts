// The Agent Navigator: an editor tab showing the current turn's agents as a
// live mind map (the main agent, its subagents, their recent tool calls).
// It keeps the turn's events, so opening it mid-turn shows everything so far.

import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { productName } from "./brand";
import type { AgentEvent } from "./protocol";
import type { FromNavigator, ToNavigator } from "./webviewMessages";

// Events the map is drawn from.
const MAP_EVENTS = new Set(["subagent_status", "tool_use", "tool_result", "permission_denied", "result"]);
const MAX_EVENTS = 5000; // a very long turn keeps its latest events

export class AgentNavigator {
  static readonly viewType = "cmcoder.navigator";

  private panel?: vscode.WebviewPanel;
  private turn: AgentEvent[] = [];
  private prompt = "";
  private model = "";
  private busy = false;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly stopSubagent: (id: string) => void,
    private readonly openChat: () => void,
  ) {}

  /** A message was sent: a new turn starts on the map. */
  startTurn(prompt: string): void {
    this.turn = [];
    this.prompt = prompt;
    this.busy = true;
    this.post({ kind: "reset", prompt, model: this.model, busy: true });
  }

  /** Every protocol event from cmcoder; the map keeps the ones it draws. */
  event(event: AgentEvent): void {
    if (event.type === "system_init" || event.type === "model_changed") this.model = event.model;
    if (!MAP_EVENTS.has(event.type)) return;
    if (event.type === "result") this.busy = false;
    this.turn.push(event);
    if (this.turn.length > MAX_EVENTS) this.turn.splice(0, this.turn.length - MAX_EVENTS);
    this.post({ kind: "event", event });
  }

  /** A new conversation: an empty map. */
  reset(): void {
    this.turn = [];
    this.prompt = "";
    this.busy = false;
    this.post({ kind: "reset", prompt: "", model: this.model, busy: false });
  }

  open(): void {
    if (this.panel) {
      this.panel.reveal(undefined, true);
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      AgentNavigator.viewType,
      `${productName()}: Agent Navigator`,
      { viewColumn: vscode.ViewColumn.Beside, preserveFocus: true },
      {
        enableScripts: true,
        retainContextWhenHidden: true,
        localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist"), vscode.Uri.joinPath(this.extensionUri, "media")],
      },
    );
    panel.webview.html = this.html(panel.webview);
    panel.webview.onDidReceiveMessage((m: FromNavigator) => {
      if (m.kind === "ready") this.replay();
      else if (m.kind === "stopSubagent") this.stopSubagent(m.id);
      else if (m.kind === "openChat") this.openChat();
    });
    panel.onDidDispose(() => (this.panel = undefined));
    this.panel = panel;
  }

  dispose(): void {
    this.panel?.dispose();
  }

  private replay(): void {
    this.post({ kind: "reset", prompt: this.prompt, model: this.model, busy: this.busy });
    for (const event of this.turn) this.post({ kind: "event", event });
  }

  private post(message: ToNavigator): void {
    void this.panel?.webview.postMessage(message);
  }

  private html(webview: vscode.Webview): string {
    const nonce = randomBytes(16).toString("hex");
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "dist", "navigator.js"));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "navigator.css"));
    const csp = ["default-src 'none'", `style-src ${webview.cspSource}`, `script-src 'nonce-${nonce}'`].join("; ");
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="${style}" rel="stylesheet">
  <title>Agent Navigator</title>
</head>
<body>
  <div id="app"></div>
  <script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}
