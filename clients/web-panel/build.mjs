// Builds the shared panel into dist/ (or the folder given to buildPanel):
//   chat.js, chat.css           the chat panel
//   navigator.js, navigator.css the Agent Navigator
//   test-driver.js              only for tests: lets an IDE's tests read and click the
//                               panel through its embedded browser (never shipped)
// Every IDE takes these files as they are. --tests bundles test/*.test.ts into
// dist-test/ for `node --test`.
import * as esbuild from "esbuild";
import { copyFileSync, mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));

export async function buildPanel(outdir = join(here, "dist"), { production = false } = {}) {
  mkdirSync(outdir, { recursive: true });
  const browser = {
    bundle: true,
    sourcemap: !production,
    minify: production,
    logLevel: "warning",
    platform: "browser",
    format: "iife",
    target: "es2020", // what JCEF, WebView2 and Edge in SWT all run
    absWorkingDir: here,
  };
  await Promise.all([
    esbuild.build({ ...browser, entryPoints: ["src/chat/main.ts"], outfile: join(outdir, "chat.js") }),
    esbuild.build({ ...browser, entryPoints: ["src/navigator/main.ts"], outfile: join(outdir, "navigator.js") }),
    esbuild.build({ ...browser, entryPoints: ["src/test-driver.ts"], outfile: join(outdir, "test-driver.js") }),
  ]);
  copyFileSync(join(here, "src/chat/chat.css"), join(outdir, "chat.css"));
  copyFileSync(join(here, "src/navigator/navigator.css"), join(outdir, "navigator.css"));
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  if (process.argv.includes("--tests")) {
    rmSync(join(here, "dist-test"), { recursive: true, force: true }); // no stale tests
    await esbuild.build({
      bundle: true,
      sourcemap: true,
      logLevel: "warning",
      entryPoints: readdirSync(join(here, "test"))
        .filter((f) => f.endsWith(".test.ts"))
        .map((f) => join(here, "test", f)),
      outdir: join(here, "dist-test"),
      platform: "node",
      format: "cjs",
      target: "node20",
      packages: "external",
    });
  } else {
    await buildPanel(join(here, "dist"), { production: process.argv.includes("--production") });
  }
}
