// Bundles the extension (Node) and the chat webview (browser) into dist/.
// --tests bundles test/*.test.ts into dist-test/ for `node --test`.
import * as esbuild from "esbuild";
import { readdirSync } from "node:fs";

const args = new Set(process.argv.slice(2));
const production = args.has("--production");
const common = { bundle: true, sourcemap: !production, minify: production, logLevel: "info" };

const builds = args.has("--tests")
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
        external: ["vscode"],
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
    ];

if (args.has("--watch")) {
  for (const b of builds) await (await esbuild.context(b)).watch();
} else {
  await Promise.all(builds.map((b) => esbuild.build(b)));
}
