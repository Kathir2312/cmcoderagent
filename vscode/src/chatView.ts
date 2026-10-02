// The chat panel in the side bar: owns the cmcoder process for this window
// and relays between it and the webview.

import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { AgentProcess } from "./agentProcess";
import type { AgentEvent } from "./protocol";
import type { AgentState, FromWebview, ToWebview } from "./webviewMessages";

export class ChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "cmcoder.chat";

  private view?: vscode.WebviewView;
  private agent?: AgentProcess;
  private state: AgentState = "exited";

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: vscode.OutputChannel,
  ) {}

  resolveWebviewView(view: vscode.WebviewView): void {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extensionUri, "dist"), vscode.Uri.joinPath(this.extensionUri, "media")],
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((m: FromWebview) => this.onWebviewMessage(m));
  }

  // --- commands ----------------------------------------------------------

  async newConversation(): Promise<void> {
    await this.stopAgent();
    this.post({ kind: "reset" });
    this.startAgent();
  }

  interrupt(): void {
    this.agent?.send({ type: "interrupt" });
  }

  async dispose(): Promise<void> {
    await this.stopAgent();
  }

  // --- the cmcoder process -------------------------------------------------

  private startAgent(): void {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      this.setState("exited", "Open a folder first: cmcoder works on the files of a project.");
      return;
    }
    const config = vscode.workspace.getConfiguration("cmcoder");
    const command = config.get<string>("executable") || "cmcoder";
    const args = config.get<string[]>("executableArgs") ?? [];
    const mode = config.get<string>("permissionMode");
    const extraArgs = mode ? ["--permission-mode", mode] : [];
    this.log.appendLine(`Starting ${[command, ...args, "--protocol", "stdio", ...extraArgs].join(" ")} in ${folder.uri.fsPath}`);
    this.setState("starting");
    const agent: AgentProcess = new AgentProcess({
      command,
      args,
      extraArgs,
      cwd: folder.uri.fsPath,
      onEvent: (event) => this.onAgentEvent(event),
      onLog: (line) => this.log.appendLine(line),
      onExit: ({ code, expected, error }) => {
        if (this.agent !== agent) return; // an old process, already replaced
        this.agent = undefined;
        this.log.appendLine(`cmcoder exited (code ${code})`);
        if (!expected) {
          this.setState("exited", error ?? `cmcoder stopped unexpectedly (exit code ${code}). See "cmcoder: Show Log".`);
        }
      },
    });
    this.agent = agent;
  }

  private async stopAgent(): Promise<void> {
    const agent = this.agent;
    this.agent = undefined;
    await agent?.stop();
  }

  private onAgentEvent(event: AgentEvent): void {
    if (event.type === "system_init") this.setState("ready");
    this.post({ kind: "event", event });
  }

  private setState(state: AgentState, message?: string): void {
    this.state = state;
    this.post({ kind: "state", state, message });
  }

  // --- the webview ---------------------------------------------------------

  private onWebviewMessage(m: FromWebview): void {
    switch (m.kind) {
      case "ready":
        // The webview (re)loaded: start a conversation if none is running.
        if (!this.agent) this.startAgent();
        else this.post({ kind: "state", state: this.state });
        break;
      case "send":
        if (!this.agent?.send({ type: "user_message", text: m.text })) {
          this.setState("exited", "cmcoder isn't running. Click Restart.");
        }
        break;
      case "interrupt":
        this.interrupt();
        break;
      case "permission":
        this.agent?.send({
          type: "permission_response",
          request_id: m.requestId,
          allow: m.allow,
          remember: m.remember,
          feedback: m.feedback || null,
        });
        break;
      case "setMode":
        this.agent?.send({ type: "set_mode", mode: m.mode });
        break;
      case "newConversation":
        void this.newConversation();
        break;
      case "restart":
        void this.stopAgent().then(() => this.startAgent());
        break;
      case "openLink":
        if (/^https?:\/\//i.test(m.href)) void vscode.env.openExternal(vscode.Uri.parse(m.href));
        break;
    }
  }

  private post(message: ToWebview): void {
    void this.view?.webview.postMessage(message);
  }

  private html(webview: vscode.Webview): string {
    const nonce = makeNonce();
    const script = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "dist", "webview.js"));
    const style = webview.asWebviewUri(vscode.Uri.joinPath(this.extensionUri, "media", "chat.css"));
    // No inline scripts, no remote content: model output can't run code here.
    const csp = [
      "default-src 'none'",
      `style-src ${webview.cspSource}`,
      `img-src ${webview.cspSource} data:`,
      `script-src 'nonce-${nonce}'`,
    ].join("; ");
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="Content-Security-Policy" content="${csp}">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="${style}" rel="stylesheet">
  <title>cmcoder</title>
</head>
<body>
  <div id="app"></div>
  <script nonce="${nonce}" src="${script}"></script>
</body>
</html>`;
  }
}

function makeNonce(): string {
  return randomBytes(16).toString("hex");
}
