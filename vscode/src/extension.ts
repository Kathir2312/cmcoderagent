// Entry point: registers the chat view and the commands.

import * as os from "node:os";
import * as path from "node:path";
import * as vscode from "vscode";
import { ChatViewProvider } from "./chatView";

let chat: ChatViewProvider | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const log = vscode.window.createOutputChannel("cmcoder");
  chat = new ChatViewProvider(context.extensionUri, log);
  context.subscriptions.push(
    log,
    vscode.window.registerWebviewViewProvider(ChatViewProvider.viewId, chat, {
      // Keep the conversation on screen when the panel is hidden and shown again.
      webviewOptions: { retainContextWhenHidden: true },
    }),
    vscode.commands.registerCommand("cmcoder.newConversation", () => chat?.newConversation()),
    vscode.commands.registerCommand("cmcoder.stop", () => chat?.interrupt()),
    vscode.commands.registerCommand("cmcoder.focus", () =>
      vscode.commands.executeCommand(`${ChatViewProvider.viewId}.focus`),
    ),
    vscode.commands.registerCommand("cmcoder.showLog", () => log.show()),
    vscode.commands.registerCommand("cmcoder.openSettings", openSettings),
  );
}

export async function deactivate(): Promise<void> {
  await chat?.dispose();
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
