// Bundles the extension (Node) and the chat webview (browser) into dist/.
// --tests bundles test/*.test.ts into dist-test/ for `node --test`
// (run `npm run build` first: the webview test loads dist/webview.js).
// --integration bundles the real-VS Code tests into dist-integration/.
import * as esbuild from "esbuild";
import { readdirSync } from "node:fs";

const args = new Set(process.argv.slice(2));
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
      {
        ...common,
        entryPoints: ["src/webview/main.ts"],
        outfile: "dist/webview.js",
        platform: "browser",
        format: "iife",
        target: "es2022",
      },
      {
        ...common,
        entryPoints: ["src/navigator/main.ts"],
        outfile: "dist/navigator.js",
        platform: "browser",
        format: "iife",
        target: "es2022",
      },
    ];

if (args.has("--watch")) {
  for (const b of builds) await (await esbuild.context(b)).watch();
} else {
  await Promise.all(builds.map((b) => esbuild.build(b)));
}
