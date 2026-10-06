// What goes into the .vsix: every file the extension loads at run time must
// be packaged. The other tests load dist/*.js from the build folder, so a file
// .vscodeignore leaves out would only fail after installing the .vsix.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const root = join(__dirname, "..");

test("the .vsix contains every script and style the extension loads", () => {
  const listed = execFileSync(process.execPath, [join(root, "node_modules", "@vscode", "vsce", "vsce"), "ls"], {
    cwd: root,
    encoding: "utf8",
  })
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const main = (JSON.parse(readFileSync(join(root, "package.json"), "utf8")) as { main: string }).main.replace(/^\.\//, "");
  const needed = new Set([main.endsWith(".js") ? main : `${main}.js`]);
  // Webview files: Uri.joinPath(this.extensionUri, "dist" | "media", "<file>").
  for (const file of readdirSync(join(root, "src")).filter((f) => f.endsWith(".ts"))) {
    const source = readFileSync(join(root, "src", file), "utf8");
    for (const m of source.matchAll(/joinPath\(this\.extensionUri, "(dist|media)", "([^"]+)"\)/g)) needed.add(`${m[1]}/${m[2]}`);
  }
  assert.ok(needed.has("dist/navigator.js") && needed.has("dist/chat.js") && needed.has("dist/chat.css"), [...needed].join(", "));
  const missing = [...needed].filter((f) => !listed.includes(f));
  assert.deepEqual(missing, [], `left out of the .vsix (see .vscodeignore): ${missing.join(", ")}`);
});
