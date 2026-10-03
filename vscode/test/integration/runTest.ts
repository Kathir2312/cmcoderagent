// Runs the integration suite in a real VS Code (downloaded by @vscode/test-electron),
// with the real cmcoder and the mock model server. On Linux, run under xvfb-run.
//   uv run --project .. npm run test:integration   (or set CMCODER_TEST_PYTHON)

import { spawn, spawnSync } from "node:child_process";
import { existsSync, mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { runTests } from "@vscode/test-electron";

const API_KEY = "sk-test-key";

async function main(): Promise<void> {
  const python = process.env.CMCODER_TEST_PYTHON || "python";
  const exe = spawnSync(python, ["-c", "import sys; print(sys.executable)"], { encoding: "utf8" });
  if (exe.status !== 0) throw new Error(`No Python with cmcoder: ${exe.stderr}`);
  const pythonPath = exe.stdout.trim();

  // The model's replies, in order (see suite.ts).
  const dir = mkdtempSync(join(tmpdir(), "cmcoder-it-"));
  const script = join(dir, "script.json");
  writeFileSync(
    script,
    JSON.stringify([
      { tool_calls: [{ name: "Read", arguments: { file_path: "app.py" } }] },
      { tool_calls: [{ name: "Edit", arguments: { file_path: "app.py", old_string: "x = 1", new_string: "x = 2" } }] },
      { content: "Changed x to 2." },
      { tool_calls: [{ name: "getDiagnostics", arguments: { file_path: "app.py" } }] },
      { content: "Checked the problems." },
    ]),
  );
  const server = spawn(pythonPath, ["-m", "cmcoder.testing.mock_server", "--script", script, "--port", "0", "--api-key", API_KEY], {
    stdio: ["ignore", "pipe", "inherit"],
  });
  const baseUrl = await new Promise<string>((ok, fail) => {
    server.on("error", fail);
    server.stdout!.setEncoding("utf8");
    server.stdout!.on("data", (d: string) => {
      const m = /mock server on (\S+)/.exec(d);
      if (m) ok(m[1]);
    });
  });

  const workspace = join(dir, "project");
  mkdirSync(join(workspace, ".git"), { recursive: true });
  mkdirSync(join(workspace, ".vscode"));
  writeFileSync(join(workspace, "app.py"), "x = 1\n");
  // CMCODER_TEST_BUNDLED: no executable setting, so the extension starts the
  // cmcoder bundled in bin/ (packaging/vsix.py puts it there); else this Python.
  const bundled = Boolean(process.env.CMCODER_TEST_BUNDLED);
  if (bundled && !existsSync(resolve(__dirname, "..", "bin", "cmcoder"))) {
    throw new Error("CMCODER_TEST_BUNDLED is set but vscode/bin/cmcoder is missing");
  }
  writeFileSync(
    join(workspace, ".vscode", "settings.json"),
    JSON.stringify(
      bundled ? {} : { "cmcoder.executable": pythonPath, "cmcoder.executableArgs": ["-m", "cmcoder"] },
    ),
  );

  try {
    await runTests({
      extensionDevelopmentPath: resolve(__dirname, ".."),
      extensionTestsPath: resolve(__dirname, "suite.js"),
      launchArgs: [workspace, "--disable-extensions", "--disable-workspace-trust"],
      extensionTestsEnv: {
        CMCODER_BASE_URL: baseUrl,
        CMCODER_API_KEY: API_KEY,
        CMCODER_MODEL: "qwen3-27b",
        CMCODER_CONFIG_DIR: join(dir, "config"),
        PYTHON_KEYRING_BACKEND: "keyring.backends.fail.Keyring",
        NO_PROXY: "127.0.0.1,localhost",
      },
    });
  } finally {
    server.kill();
  }
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
