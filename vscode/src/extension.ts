// Entry point: registers the chat view, the diff review and the commands.

import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { ChatViewProvider } from "./chatView";
import { DiffReview, SCHEME } from "./diffReview";

let chat: ChatViewProvider | undefined;

/** What `activate` returns: used by the integration tests to drive the chat. */
export interface CmcoderApi {
  onEvent: ChatViewProvider["onEvent"];
  send(text: string): boolean;
}

export function activate(context: vscode.ExtensionContext): CmcoderApi {
  const log = vscode.window.createOutputChannel("cmcoder");
  const diffs = new DiffReview();
  chat = new ChatViewProvider(context.extensionUri, log, diffs);
  const view = chat;

  // Keep the panel's "context" chip in step with the editor (debounced).
  let timer: NodeJS.Timeout | undefined;
  const contextChanged = () => {
    clearTimeout(timer);
    timer = setTimeout(() => view.updateContext(), 300);
  };

  context.subscriptions.push(
    log,
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, diffs),
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, view, {
      // Keep the conversation on screen when the panel is hidden and shown again.
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.window.onDidChangeActiveTextEditor(contextChanged),
    vscode.window.onDidChangeTextEditorSelection(contextChanged),
    vscode.languages.onDidChangeDiagnostics(contextChanged),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("cmcoder.autoContext")) contextChanged();
    }),
    vscode.commands.registerCommand("cmcoder.newConversation", () => view.newConversation()),
    vscode.commands.registerCommand("cmcoder.continueConversation", () => view.newConversation(["--continue"])),
    vscode.commands.registerCommand("cmcoder.stop", () => view.interrupt()),
    vscode.commands.registerCommand("cmcoder.focus", () =>
      vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`),
    ),
    vscode.commands.registerCommand("cmcoder.askAboutSelection", () => view.askAboutSelection()),
    vscode.commands.registerCommand("cmcoder.attachFile", () => view.attachFile()),
    vscode.commands.registerCommand("cmcoder.acceptChange", () => {
      const id = diffs.activeRequest();
      if (id) view.answer(id, true);
    }),
    vscode.commands.registerCommand("cmcoder.rejectChange", async () => {
      const id = diffs.activeRequest();
      if (!id) return;
      const feedback = await vscode.window.showInputBox({
        prompt: "Rejecting the change. Tell cmcoder what to do instead (optional).",
      });
      if (feedback === undefined) return; // Esc: keep reviewing
      view.answer(id, false, false, feedback);
    }),
    vscode.commands.registerCommand("cmcoder.openTerminal", openTerminal),
    vscode.commands.registerCommand("cmcoder.showLog", () => log.show()),
    vscode.commands.registerCommand("cmcoder.openSettings", openSettings),
  );
  return { onEvent: view.onEvent, send: (text) => view.sendText(text, true) };
}

export async function deactivate(): Promise<void> {
  await chat?.dispose();
}

/** Runs the cmcoder CLI in VS Code's integrated terminal. */
function openTerminal(): void {
  const config = vscode.workspace.getConfiguration("cmcoder");
  const command = config.get<string>("executable") || "cmcoder";
  const args = config.get<string[]>("executableArgs") ?? [];
  const terminal = vscode.window.createTerminal({
    name: "cmcoder",
    cwd: vscode.workspace.workspaceFolders?.[0]?.uri,
    // The program itself, not a shell command line: no quoting problems.
    shellPath: command,
    shellArgs: args,
    iconPath: new vscode.ThemeIcon("code"),
  });
  terminal.show();
}

/** Opens cmcoder's own settings file (the same one the CLI uses). */
async function openSettings(): Promise<void> {
  const dir = process.env.CMCODER_CONFIG_DIR || path.join(os.homedir(), ".cmcoder");
  const uri = vscode.Uri.file(path.join(dir, "settings.json"));
  try {
    await vscode.workspace.fs.stat(uri);
  } catch {
    await vscode.workspace.fs.writeFile(uri, new TextEncoder().encode("{\n}\n"));
  }
  await vscode.window.showTextDocument(uri);
}
