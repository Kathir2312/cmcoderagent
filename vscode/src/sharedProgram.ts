// The shared cmcoder in ~/.cmcoder/bin (on Windows %USERPROFILE%\.cmcoder\bin):
// one program for the terminal and every IDE. The terminal package installs
// there, and a cmcoder started from an extension's own copy copies itself
// there when that one is missing or older (src/cmcoder/shared_install.py).
// "Newer" is the build's STAMP. No VS Code API here: unit-tested on its own.

import { existsSync, readFileSync } from "node:fs";
import * as os from "node:os";
import * as path from "node:path";

function exeName(): string {
  return process.platform === "win32" ? "cmcoder.exe" : "cmcoder";
}

export function bundledCmcoder(extensionPath: string): string | undefined {
  const file = path.join(extensionPath, "bin", "cmcoder", exeName());
  return existsSync(file) ? file : undefined;
}

/** The folder of the shared cmcoder: CMCODER_CONFIG_DIR (or ~/.cmcoder), then bin. */
export function sharedFolder(env: NodeJS.ProcessEnv = process.env, home: string = os.homedir()): string {
  return path.join(env.CMCODER_CONFIG_DIR || path.join(home, ".cmcoder"), "bin");
}

/** The build stamp of the cmcoder in `folder` (sortable), or undefined if there's none. */
export function stampOf(folder: string): string | undefined {
  if (!existsSync(path.join(folder, exeName()))) return undefined;
  for (const file of [path.join(folder, "_internal", "cmcoder", "STAMP"), path.join(folder, "cmcoder", "STAMP")]) {
    try {
      const text = readFileSync(file, "utf8").trim();
      if (text) return text;
    } catch {
      // not this layout
    }
  }
  return undefined;
}

/** The program to run when nothing is set: the shared one if it's at least as new as ours. */
export function chooseProgram(extensionPath: string, shared: string = sharedFolder()): string | undefined {
  const ours = bundledCmcoder(extensionPath);
  const sharedStamp = stampOf(shared);
  if (sharedStamp !== undefined) {
    const ourStamp = ours ? stampOf(path.dirname(ours)) : undefined;
    if (ourStamp === undefined || sharedStamp >= ourStamp) return path.join(shared, exeName());
  }
  return ours;
}
