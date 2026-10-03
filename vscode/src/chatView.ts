// The chat panel in the side bar: owns the cmcoder process for this window
// and relays between it, the webview and the editor.

import { randomBytes } from "node:crypto";
import * as vscode from "vscode";
import { AgentProcess } from "./agentProcess";
import { DiffReview } from "./diffReview";
import { contextLabel, currentEditor, editorContext, IDE_TOOLS, runIdeTool } from "./editorContext";
import type { AgentEvent, FileChange } from "./protocol";
import type { AgentState, FromWebview, ToWebview } from "./webviewMessages";

export class ChatViewProvider implements vscode.WebviewViewProvider {
  static readonly viewId = "cmcoder.chat";

  private view?: vscode.WebviewView;
  private agent?: AgentProcess;
  private state: AgentState = "exited";
  /** Open permission requests that have a diff to review. */
  private readonly changes = new Map<string, FileChange>();
  private readonly events = new vscode.EventEmitter<AgentEvent>();
  /** Every protocol event from cmcoder (used by the integration tests). */
  readonly onEvent = this.events.event;

  constructor(
    private readonly extensionUri: vscode.Uri,
    private readonly log: vscode.OutputChannel,
    private readonly diffs: DiffReview,
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

  /** Sends a message, with the editor context when asked and enabled. */
  sendText(text: string, includeContext: boolean): boolean {
    const enabled = vscode.workspace.getConfiguration("cmcoder").get<boolean>("autoContext", true);
    const context = includeContext && enabled ? editorContext(currentEditor()) : null;
    if (this.agent?.send({ type: "user_message", text, context })) return true;
    this.setState("exited", "cmcoder isn't running. Click Restart.");
    return false;
  }

  async newConversation(extraArgs: string[] = []): Promise<void> {
    await this.stopAgent();
    this.post({ kind: "reset" });
    this.startAgent(extraArgs);
  }

  interrupt(): void {
    this.agent?.send({ type: "interrupt" });
  }

  /** Answers a permission request (from the panel or the diff editor). */
  answer(requestId: string, allow: boolean, remember = false, feedback?: string): void {
    this.agent?.send({ type: "permission_response", request_id: requestId, allow, remember, feedback: feedback || null });
    this.changes.delete(requestId);
    void this.diffs.close(requestId);
    this.post({
      kind: "permissionAnswered",
      requestId,
      text: allow ? (remember ? "Allowed (always)" : "Allowed") : "Denied",
    });
  }

  /** "Ask cmcoder about selection": focus the chat with the selection as context. */
  async askAboutSelection(): Promise<void> {
    await vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`);
    this.updateContext();
    this.post({ kind: "prefill", text: "Explain the selected code. " });
  }

  async attachFile(): Promise<void> {
    const files = await vscode.workspace.findFiles("**/*", "**/{node_modules,.git,.venv,__pycache__}/**", 5000);
    const items = files
      .map((f) => vscode.workspace.asRelativePath(f).replace(/\\/g, "/"))
      .sort()
      .map((label) => ({ label }));
    const pick = await vscode.window.showQuickPick(items, { placeHolder: "Attach a file to the message (@)" });
    if (pick) this.post({ kind: "prefill", text: `@${pick.label} ` });
  }

  /** The editor's context changed (active file, selection, problems). */
  updateContext(): void {
    const enabled = vscode.workspace.getConfiguration("cmcoder").get<boolean>("autoContext", true);
    this.post({ kind: "context", label: enabled ? contextLabel(editorContext()) : null });
  }

  async dispose(): Promise<void> {
    await this.stopAgent();
  }

  // --- the cmcoder process -------------------------------------------------

  private startAgent(extraArgs: string[] = []): void {
    const folder = vscode.workspace.workspaceFolders?.[0];
    if (!folder) {
      this.setState("exited", "Open a folder first: cmcoder works on the files of a project.");
      return;
    }
    const config = vscode.workspace.getConfiguration("cmcoder");
    const command = config.get<string>("executable") || "cmcoder";
    const args = config.get<string[]>("executableArgs") ?? [];
    const mode = config.get<string>("permissionMode");
    const allArgs = [...(mode ? ["--permission-mode", mode] : []), ...extraArgs];
    this.log.appendLine(`Starting ${[command, ...args, "--protocol", "stdio", ...allArgs].join(" ")} in ${folder.uri.fsPath}`);
    this.setState("starting");
    const agent: AgentProcess = new AgentProcess({
      command,
      args,
      extraArgs: allArgs,
      cwd: folder.uri.fsPath,
      onEvent: (event) => {
        if (this.agent === agent) void this.onAgentEvent(event);
      },
      onLog: (line) => this.log.appendLine(line),
      onExit: ({ code, expected, error }) => {
        if (this.agent !== agent) return; // an old process, already replaced
        this.agent = undefined;
        void this.clearReviews();
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
    await this.clearReviews();
    await agent?.stop();
  }

  private async clearReviews(): Promise<void> {
    this.changes.clear();
    await this.diffs.closeAll();
  }

  private async onAgentEvent(event: AgentEvent): Promise<void> {
    // The panel sees every event first and in order; side effects come after.
    this.post({ kind: "event", event });
    this.events.fire(event);
    switch (event.type) {
      case "system_init":
        this.setState("ready");
        this.agent?.send({ type: "ide_capabilities", tools: IDE_TOOLS });
        this.updateContext();
        break;
      case "permission_request":
        if (event.change) {
          this.changes.set(event.request_id, event.change);
          if (vscode.workspace.getConfiguration("cmcoder").get<boolean>("diffReview", true)) {
            void this.diffs.open(event.request_id, event.change);
          }
        }
        break;
      case "ide_tool_request": {
        const result = await runIdeTool(event.name, event.input);
        this.agent?.send({ type: "ide_tool_result", request_id: event.request_id, ...result });
        break;
      }
      case "result":
        await this.clearReviews(); // an interrupted turn leaves no open requests
        break;
    }
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
        this.updateContext();
        break;
      case "send":
        this.sendText(m.text, m.includeContext);
        break;
      case "interrupt":
        this.interrupt();
        break;
      case "permission":
        this.answer(m.requestId, m.allow, m.remember, m.feedback);
        break;
      case "showDiff": {
        const change = this.changes.get(m.requestId);
        if (change) void this.diffs.open(m.requestId, change);
        break;
      }
      case "setMode":
        this.agent?.send({ type: "set_mode", mode: m.mode });
        break;
      case "newConversation":
        void this.newConversation();
        break;
      case "listSessions":
        this.agent?.send({ type: "list_sessions" });
        break;
      case "resume":
        void this.newConversation(["--resume", m.id]);
        break;
      case "attachFile":
        void this.attachFile();
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
    const nonce = randomBytes(16).toString("hex");
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
