// Which cmcoder the extension starts: one you set (cmcoder.executable), else
// the one bundled in a platform-specific .vsix (bin/cmcoder/), else `cmcoder`
// on PATH.

import { existsSync } from "node:fs";
import * as path from "node:path";
import * as vscode from "vscode";

export function bundledCmcoder(extensionPath: string): string | undefined {
  const exe = process.platform === "win32" ? "cmcoder.exe" : "cmcoder";
  const file = path.join(extensionPath, "bin", "cmcoder", exe);
  return existsSync(file) ? file : undefined;
}

export function cmcoderCommand(extensionPath: string): { command: string; args: string[] } {
  const config = vscode.workspace.getConfiguration("cmcoder");
  const set = config.inspect<string>("executable");
  const chosen = set?.globalValue ?? set?.workspaceValue ?? set?.workspaceFolderValue;
  const args = config.get<string[]>("executableArgs") ?? [];
  if (!chosen) {
    const bundled = bundledCmcoder(extensionPath);
    if (bundled) return { command: bundled, args: [] };
  }
  return { command: chosen || "cmcoder", args };
}
