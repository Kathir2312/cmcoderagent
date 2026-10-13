// One `cmcoder --protocol stdio` child process: one conversation.
// Plain Node (no `vscode` import), so it can be tested without VS Code.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import { statSync } from "node:fs";
import * as path from "node:path";
import type { AgentEvent, ClientMessage } from "./protocol";

export interface AgentProcessOptions {
  /** The program to run, e.g. "cmcoder" or "uv". */
  command: string;
  /** Arguments before cmcoder's own, e.g. ["run", "--project", "...", "cmcoder"]. */
  args?: string[];
  /** Extra cmcoder arguments, e.g. ["--continue"]. */
  extraArgs?: string[];
  cwd: string;
  env?: NodeJS.ProcessEnv;
  onEvent: (event: AgentEvent) => void;
  /** stderr and anything on stdout that isn't a protocol event. */
  onLog?: (line: string) => void;
  /** The process ended. `expected` is false for a crash or a failed start. */
  onExit?: (info: { code: number | null; expected: boolean; error?: string }) => void;
}

export class AgentProcess {
  private proc?: ChildProcessWithoutNullStreams;
  private stopping = false;
  private exited = false;
  private readonly exitPromise: Promise<void>;

  constructor(private readonly opts: AgentProcessOptions) {
    const args = [...(opts.args ?? []), "--protocol", "stdio", ...(opts.extraArgs ?? [])];
    const env = { ...process.env, ...opts.env, NO_COLOR: "1" };
    // A full path, never a program planted in the workspace (see resolveExecutable).
    const program = resolveExecutable(opts.command, opts.cwd, env);
    let startError: string | undefined;
    let finish: (code: number | null) => void = () => {};
    this.exitPromise = new Promise((resolve) => {
      finish = (code: number | null) => {
        if (this.exited) return;
        this.exited = true;
        opts.onExit?.({ code, expected: this.stopping && !startError, error: startError });
        resolve();
      };
    });
    if (!program) {
      startError = describeSpawnError(opts.command, Object.assign(new Error("not found"), { code: "ENOENT" }));
      setImmediate(() => finish(null));
      return;
    }
    const proc = spawn(program, args, {
      cwd: opts.cwd,
      env,
      // No console window on Windows; no shell, so arguments are never re-parsed.
      windowsHide: true,
      shell: false,
    });
    this.proc = proc;
    {
      proc.on("error", (err) => {
        // e.g. ENOENT: the command isn't installed or not on PATH.
        startError = describeSpawnError(program, err);
        finish(null);
      });
      proc.on("close", (code, signal) => {
        if (signal) opts.onLog?.(`cmcoder ended by signal ${signal}`);
        finish(code);
      });
    }
    lines(proc.stdout, (line) => this.onStdout(line));
    lines(proc.stderr, (line) => opts.onLog?.(line));
    proc.stdin.on("error", () => {
      /* the process went away; onExit reports it */
    });
  }

  get running(): boolean {
    return !this.exited;
  }

  send(message: ClientMessage): boolean {
    if (this.exited || !this.proc || this.proc.stdin.destroyed) return false;
    this.proc.stdin.write(JSON.stringify(message) + "\n");
    return true;
  }

  /** Ask the agent to finish (it saves the session), then make sure it's gone. */
  async stop(timeoutMs = 5000): Promise<void> {
    if (this.exited) return;
    this.stopping = true;
    const proc = this.proc;
    if (proc) {
      this.send({ type: "shutdown" });
      proc.stdin.end();
    }
    const timer = setTimeout(() => proc?.kill(), timeoutMs);
    await this.exitPromise;
    clearTimeout(timer);
  }

  private onStdout(line: string): void {
    if (!line.trim()) return;
    let event: AgentEvent;
    try {
      event = JSON.parse(line) as AgentEvent;
    } catch {
      this.opts.onLog?.(`[stdout] ${line}`);
      return;
    }
    if (typeof event !== "object" || event === null || typeof event.type !== "string") {
      this.opts.onLog?.(`[stdout] ${line}`);
      return;
    }
    this.opts.onEvent(event);
  }
}

/** Calls `onLine` for each complete line, decoding UTF-8 across chunk boundaries. */
function lines(stream: NodeJS.ReadableStream, onLine: (line: string) => void): void {
  let buffer = "";
  stream.setEncoding("utf8");
  stream.on("data", (chunk: string) => {
    buffer += chunk;
    let i: number;
    while ((i = buffer.indexOf("\n")) >= 0) {
      onLine(buffer.slice(0, i).replace(/\r$/, ""));
      buffer = buffer.slice(i + 1);
    }
  });
  stream.on("end", () => {
    if (buffer) onLine(buffer);
    buffer = "";
  });
}

export function describeSpawnError(command: string, err: NodeJS.ErrnoException): string {
  if (err.code === "ENOENT") {
    return (
      `Couldn't start "${command}": not found. Install the cmcoder extension file for your platform ` +
      "(cmcoder-win32-x64.vsix and so on: it includes cmcoder), or set `cmcoder.executable` to its full path."
    );
  }
  if (err.code === "EINVAL" && /\.(cmd|bat)$/i.test(command)) {
    return `Couldn't start "${command}": batch files can't be started directly; point cmcoder.executable at the .exe.`;
  }
  return `Couldn't start "${command}": ${err.message}`;
}

/**
 * The full path of the program to start, or undefined if there is none.
 *
 * Node's spawn on Windows looks for a bare program name in the child's
 * working directory (the workspace) before PATH, so a cloned repository could
 * ship its own cmcoder.exe. Bare names are therefore looked up on PATH here,
 * skipping relative entries and the workspace itself. An explicit path
 * (absolute, or relative to the workspace, from a trusted setting) is used as
 * given.
 */
export function resolveExecutable(command: string, cwd: string, env: NodeJS.ProcessEnv = process.env): string | undefined {
  const isFile = (p: string) => {
    try {
      return statSync(p).isFile();
    } catch {
      return false;
    }
  };
  if (path.isAbsolute(command)) return isFile(command) ? command : undefined;
  if (/[\\/]/.test(command)) {
    const p = path.resolve(cwd, command);
    return isFile(p) ? p : undefined;
  }
  const windows = process.platform === "win32";
  const pathVar = env.PATH ?? env.Path ?? "";
  const here = path.resolve(cwd);
  const dirs = pathVar
    .split(path.delimiter)
    .filter((d) => d && path.isAbsolute(d))
    .filter((d) => (windows ? path.resolve(d).toLowerCase() !== here.toLowerCase() : path.resolve(d) !== here));
  const exts = windows
    ? (env.PATHEXT ?? ".COM;.EXE;.BAT;.CMD").toLowerCase().split(";").filter(Boolean)
    : [""];
  const named = windows && exts.some((e) => command.toLowerCase().endsWith(e));
  for (const dir of dirs) {
    for (const ext of named ? [""] : exts) {
      const candidate = path.join(dir, command + ext);
      if (isFile(candidate)) return candidate;
    }
  }
  return undefined;
}
