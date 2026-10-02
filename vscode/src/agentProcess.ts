// One `cmcoder --protocol stdio` child process: one conversation.
// Plain Node (no `vscode` import), so it can be tested without VS Code.

import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
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
  private proc: ChildProcessWithoutNullStreams;
  private stopping = false;
  private exited = false;
  private readonly exitPromise: Promise<void>;

  constructor(private readonly opts: AgentProcessOptions) {
    const args = [...(opts.args ?? []), "--protocol", "stdio", ...(opts.extraArgs ?? [])];
    this.proc = spawn(opts.command, args, {
      cwd: opts.cwd,
      env: { ...process.env, ...opts.env, NO_COLOR: "1" },
      // No console window on Windows; no shell, so arguments are never re-parsed.
      windowsHide: true,
      shell: false,
    });
    let startError: string | undefined;
    this.exitPromise = new Promise((resolve) => {
      const finish = (code: number | null) => {
        if (this.exited) return;
        this.exited = true;
        opts.onExit?.({ code, expected: this.stopping && !startError, error: startError });
        resolve();
      };
      this.proc.on("error", (err) => {
        // e.g. ENOENT: the command isn't installed or not on PATH.
        startError = describeSpawnError(opts.command, err);
        finish(null);
      });
      this.proc.on("close", (code, signal) => {
        if (signal) opts.onLog?.(`cmcoder ended by signal ${signal}`);
        finish(code);
      });
    });
    lines(this.proc.stdout, (line) => this.onStdout(line));
    lines(this.proc.stderr, (line) => opts.onLog?.(line));
    this.proc.stdin.on("error", () => {
      /* the process went away; onExit reports it */
    });
  }

  get running(): boolean {
    return !this.exited;
  }

  send(message: ClientMessage): boolean {
    if (this.exited || this.proc.stdin.destroyed) return false;
    this.proc.stdin.write(JSON.stringify(message) + "\n");
    return true;
  }

  /** Ask the agent to finish (it saves the session), then make sure it's gone. */
  async stop(timeoutMs = 5000): Promise<void> {
    if (this.exited) return;
    this.stopping = true;
    this.send({ type: "shutdown" });
    this.proc.stdin.end();
    const timer = setTimeout(() => this.proc.kill(), timeoutMs);
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
      `Couldn't start "${command}": not found. Install cmcoder ` +
      "(`uv tool install ...`) or set `cmcoder.executable` to its full path."
    );
  }
  return `Couldn't start "${command}": ${err.message}`;
}
