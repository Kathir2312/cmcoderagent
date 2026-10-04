// Entry point: registers the chat view, the diff review and the commands.

import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { resolveExecutable } from "./agentProcess";
import { productName, setBrand } from "./brand";
import { ChatViewProvider } from "./chatView";
import { DiffReview, SCHEME } from "./diffReview";
import { cmcoderCommand } from "./executable";

let chat: ChatViewProvider | undefined;

/** What `activate` returns: used by the integration tests to drive the chat. */
export interface CmcoderApi {
  onEvent: ChatViewProvider["onEvent"];
  send(text: string): boolean;
}

export function activate(context: vscode.ExtensionContext): CmcoderApi {
  setBrand(context.extension);
  const log = vscode.window.createOutputChannel(productName());
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
    vscode.commands.registerCommand("cmcoder.openTerminal", () => openTerminal(context.extensionUri)),
    vscode.commands.registerCommand("cmcoder.showLog", () => log.show()),
    vscode.commands.registerCommand("cmcoder.openSettings", openSettings),
  );
  return { onEvent: view.onEvent, send: (text) => view.sendText(text, true) };
}

export async function deactivate(): Promise<void> {
  await chat?.dispose();
}

/** Runs the cmcoder CLI in VS Code's integrated terminal. */
function openTerminal(extensionUri: vscode.Uri): void {
  const { command, args } = cmcoderCommand(extensionUri.fsPath);
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
  // A full path: never a cmcoder.exe planted in the workspace (Windows).
  const program = resolveExecutable(command, folder?.fsPath ?? process.cwd());
  if (!program) {
    void vscode.window.showErrorMessage(`${productName()}: "${command}" was not found on PATH. Set cmcoder.executable to its full path.`);
    return;
  }
  const terminal = vscode.window.createTerminal({
    name: productName(),
    cwd: folder,
    // The program itself, not a shell command line: no quoting problems.
    shellPath: program,
    shellArgs: vscode.workspace.isTrusted ? [...args, "--trust-project"] : args,
    iconPath: vscode.Uri.joinPath(extensionUri, "media", "icon.png"),
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
