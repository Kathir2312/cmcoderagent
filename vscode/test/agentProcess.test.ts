// AgentProcess against the real `cmcoder --protocol stdio` and the mock model server.
// Run from the repository with the Python environment active: `uv run npm test`
// (or set CMCODER_TEST_PYTHON to a Python that has cmcoder installed).

import assert from "node:assert/strict";
import { spawn, type ChildProcess } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { after, before, test } from "node:test";
import { AgentProcess } from "../src/agentProcess";
import type { AgentEvent } from "../src/protocol";

const PYTHON = process.env.CMCODER_TEST_PYTHON || "python";
const API_KEY = "sk-test-key";

let server: ChildProcess | undefined;
let baseUrl = "";
let configDir = "";

/** Starts the mock model server with a script of replies; resolves its base URL. */
function startMockServer(script: unknown[]): Promise<{ proc: ChildProcess; url: string }> {
  const dir = mkdtempSync(join(tmpdir(), "cmcoder-mock-"));
  const file = join(dir, "script.json");
  writeFileSync(file, JSON.stringify(script));
  const proc = spawn(
    PYTHON,
    ["-m", "cmcoder.testing.mock_server", "--script", file, "--port", "0", "--api-key", API_KEY],
    { stdio: ["ignore", "pipe", "inherit"] },
  );
  return new Promise((resolve, reject) => {
    proc.on("error", reject);
    proc.stdout!.setEncoding("utf8");
    proc.stdout!.on("data", (chunk: string) => {
      const m = /mock server on (\S+)/.exec(chunk);
      if (m) resolve({ proc, url: m[1] });
    });
  });
}

function project(): string {
  const dir = mkdtempSync(join(tmpdir(), "cmcoder-proj-"));
  mkdirSync(join(dir, ".git"));
  return dir;
}

/** An AgentProcess that collects events and lets a test wait for one. */
function startAgent(cwd: string, extraArgs: string[] = []) {
  const events: AgentEvent[] = [];
  const waiters: Array<{ type: string; resolve: (e: AgentEvent) => void }> = [];
  let exit: { code: number | null; expected: boolean; error?: string } | undefined;
  const agent = new AgentProcess({
    command: PYTHON,
    args: ["-m", "cmcoder"],
    extraArgs,
    cwd,
    env: {
      CMCODER_BASE_URL: baseUrl,
      CMCODER_API_KEY: API_KEY,
      CMCODER_MODEL: "qwen3-27b",
      CMCODER_CONFIG_DIR: configDir,
      PYTHON_KEYRING_BACKEND: "keyring.backends.fail.Keyring",
      HTTPS_PROXY: "",
      HTTP_PROXY: "",
      https_proxy: "",
      http_proxy: "",
    },
    onEvent: (e) => {
      events.push(e);
      const i = waiters.findIndex((w) => w.type === e.type);
      if (i >= 0) waiters.splice(i, 1)[0].resolve(e);
    },
    onExit: (info) => (exit = info),
    onLog: (line) => process.stderr.write(`[cmcoder] ${line}\n`),
  });
  const next = <T extends AgentEvent["type"]>(type: T) =>
    new Promise<Extract<AgentEvent, { type: T }>>((resolve, reject) => {
      const seen = events.find((e) => e.type === type && !(e as { _taken?: boolean })._taken);
      if (seen) {
        (seen as { _taken?: boolean })._taken = true;
        resolve(seen as Extract<AgentEvent, { type: T }>);
        return;
      }
      const timer = setTimeout(() => reject(new Error(`timed out waiting for ${type}`)), 30_000);
      waiters.push({
        type,
        resolve: (e) => {
          clearTimeout(timer);
          (e as { _taken?: boolean })._taken = true;
          resolve(e as Extract<AgentEvent, { type: T }>);
        },
      });
    });
  return { agent, events, next, exit: () => exit };
}

before(async () => {
  configDir = mkdtempSync(join(tmpdir(), "cmcoder-config-"));
  const write = (path: string, content: string) => ({
    tool_calls: [{ name: "Write", arguments: { file_path: path, content } }],
  });
  const started = await startMockServer([
    write("hello.py", "print('hi')\n"),
    { content: "Created hello.py." },
    write("nope.py", "x\n"),
    { content: "OK, I won't." },
  ]);
  server = started.proc;
  baseUrl = started.url;
});

after(() => {
  server?.kill();
});

test("a turn with a permission prompt, allowed then denied", async () => {
  const cwd = project();
  const { agent, next, exit } = startAgent(cwd);
  try {
    const init = await next("system_init");
    assert.equal(init.protocol_version, 1);

    agent.send({ type: "user_message", text: "create hello.py" });
    const ask = await next("permission_request");
    assert.equal(ask.name, "Write");
    agent.send({ type: "permission_response", request_id: ask.request_id, allow: true });
    const result = await next("result");
    assert.equal(result.result, "Created hello.py.");
    assert.equal(readFileSync(join(cwd, "hello.py"), "utf8"), "print('hi')\n");

    agent.send({ type: "user_message", text: "create nope.py" });
    const ask2 = await next("permission_request");
    agent.send({ type: "permission_response", request_id: ask2.request_id, allow: false, feedback: "not now" });
    await next("permission_denied");
    assert.equal((await next("result")).result, "OK, I won't.");
    assert.ok(!existsSync(join(cwd, "nope.py")));
  } finally {
    await agent.stop();
  }
  assert.deepEqual(exit(), { code: 0, expected: true, error: undefined });
});

test("a missing executable is reported, not thrown", async () => {
  let exit: { code: number | null; expected: boolean; error?: string } | undefined;
  const agent = new AgentProcess({
    command: "definitely-not-cmcoder-xyz",
    cwd: project(),
    onEvent: () => assert.fail("no events expected"),
    onExit: (info) => (exit = info),
  });
  await agent.stop();
  assert.equal(exit?.expected, false);
  assert.match(exit?.error ?? "", /not found/);
  assert.equal(agent.send({ type: "interrupt" }), false);
});

test("a crash is reported as unexpected", async () => {
  // --permission-mode with a bad value makes cmcoder exit with an error at once.
  const { exit } = startAgent(project(), ["--permission-mode", "nonsense"]);
  for (let i = 0; i < 300 && !exit(); i++) await new Promise((r) => setTimeout(r, 100));
  assert.equal(exit()?.expected, false);
  assert.equal(exit()?.code, 2);
});
