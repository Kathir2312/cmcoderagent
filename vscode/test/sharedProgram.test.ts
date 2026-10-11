// Which cmcoder the extension runs: the shared one in ~/.cmcoder/bin when it's
// at least as new as the extension's own copy (src/sharedProgram.ts).

import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { chooseProgram, sharedFolder, stampOf } from "../src/sharedProgram";

const exe = process.platform === "win32" ? "cmcoder.exe" : "cmcoder";

/** A standalone cmcoder folder (PyInstaller's layout) with this build stamp. */
function program(folder: string, stamp?: string): string {
  mkdirSync(join(folder, "_internal", "cmcoder"), { recursive: true });
  writeFileSync(join(folder, exe), "");
  if (stamp) writeFileSync(join(folder, "_internal", "cmcoder", "STAMP"), `${stamp}\n`);
  return join(folder, exe);
}

test("the shared cmcoder is run when it's at least as new as the extension's", () => {
  const root = mkdtempSync(join(tmpdir(), "cmcoder-shared-"));
  try {
    const extension = join(root, "ext");
    const shared = join(root, "home", ".cmcoder", "bin");
    const ours = program(join(extension, "bin", "cmcoder"), "20261008T120000Z");
    // Nothing shared yet: our own copy (cmcoder copies itself there for next time).
    assert.equal(chooseProgram(extension, shared), ours);
    // An older shared copy: ours, which is newer.
    const theirs = program(shared, "20261001T090000Z");
    assert.equal(stampOf(shared), "20261001T090000Z");
    assert.equal(chooseProgram(extension, shared), ours);
    // As new or newer: the shared one.
    writeFileSync(join(shared, "_internal", "cmcoder", "STAMP"), "20261008T120000Z");
    assert.equal(chooseProgram(extension, shared), theirs);
    writeFileSync(join(shared, "_internal", "cmcoder", "STAMP"), "20261009T000000Z");
    assert.equal(chooseProgram(extension, shared), theirs);
    // A shared folder without a stamp (a broken copy): ours.
    rmSync(join(shared, "_internal"), { recursive: true });
    assert.equal(stampOf(shared), undefined);
    assert.equal(chooseProgram(extension, shared), ours);
    // An extension without its own copy (the generic .vsix): the shared one, if any.
    program(shared, "20261001T090000Z");
    assert.equal(chooseProgram(join(root, "generic"), shared), theirs);
    assert.equal(chooseProgram(join(root, "generic"), join(root, "nowhere")), undefined);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});

test("the shared folder follows CMCODER_CONFIG_DIR, else the home folder", () => {
  assert.equal(sharedFolder({ CMCODER_CONFIG_DIR: join("x", "cfg") }, "home"), join("x", "cfg", "bin"));
  assert.equal(sharedFolder({}, join("C:", "Users", "me")), join("C:", "Users", "me", ".cmcoder", "bin"));
});
