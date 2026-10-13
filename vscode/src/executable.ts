// Which cmcoder the extension starts: one you set (cmcoder.executable), else
// the shared one in ~/.cmcoder/bin when it's at least as new as the copy in
// this extension (bin/cmcoder/), else that copy, else `cmcoder` on PATH
// (sharedProgram.ts).

import * as vscode from "vscode";
import { chooseProgram } from "./sharedProgram";

export { bundledCmcoder, chooseProgram } from "./sharedProgram";

export function cmcoderCommand(extensionPath: string): { command: string; args: string[] } {
  const config = vscode.workspace.getConfiguration("cmcoder");
  const set = config.inspect<string>("executable");
  const chosen = set?.globalValue ?? set?.workspaceValue ?? set?.workspaceFolderValue;
  const args = config.get<string[]>("executableArgs") ?? [];
  if (!chosen) {
    const program = chooseProgram(extensionPath);
    if (program) return { command: program, args: [] };
  }
  return { command: chosen || "cmcoder", args };
}
