// Bundles the extension (Node) into dist/, with the chat panel and the Agent
// Navigator built from the shared package (clients/web-panel: chat.js, chat.css,
// navigator.js, navigator.css), the same files the other IDEs show.
// --tests bundles test/*.test.ts into dist-test/ for `node --test`
// (the chat panel's own tests live in clients/web-panel).
// --integration bundles the real-VS Code tests into dist-integration/.
import * as esbuild from "esbuild";
import { readdirSync, rmSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { buildPanel } from "../clients/web-panel/build.mjs";

const args = new Set(process.argv.slice(2));
// Compiled tests from earlier builds (e.g. ones that moved) would run too.
if (args.has("--tests")) rmSync("dist-test", { recursive: true, force: true });
const production = args.has("--production");
const common = { bundle: true, sourcemap: !production, minify: production, logLevel: "info" };

const integration = {
  ...common,
  entryPoints: ["test/integration/runTest.ts", "test/integration/suite.ts"],
  outdir: "dist-integration",
  platform: "node",
  format: "cjs",
  target: "node20",
  packages: "external",
};

const builds = args.has("--integration")
  ? [integration]
  : args.has("--tests")
  ? [
      {
        ...common,
        entryPoints: readdirSync("test")
          .filter((f) => f.endsWith(".test.ts"))
          .map((f) => `test/${f}`),
        outdir: "dist-test",
        platform: "node",
        format: "cjs",
        target: "node20",
        // Tests load npm packages (e.g. Playwright) from node_modules, unbundled.
        packages: "external",
      },
    ]
  : [
      {
        ...common,
        entryPoints: ["src/extension.ts"],
        outfile: "dist/extension.js",
        platform: "node",
        format: "cjs",
        target: "node20",
        external: ["vscode"],
      },
    ];
const panel = !args.has("--integration") && !args.has("--tests");

if (args.has("--watch")) {
  for (const b of builds) await (await esbuild.context(b)).watch();
} else {
  await Promise.all([
    ...builds.map((b) => esbuild.build(b)),
    ...(panel ? [buildPanel(fileURLToPath(new URL("./dist", import.meta.url)), { production })] : []),
  ]);
}
