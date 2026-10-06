// theme.json is the list of colours and fonts every IDE must give the panel.
// These checks keep it exact: a colour the panel starts using without listing it
// would be missing (unstyled) in JetBrains, Visual Studio and Eclipse.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { test } from "node:test";

const root = join(__dirname, "..");
const theme = JSON.parse(readFileSync(join(root, "theme.json"), "utf8")) as {
  variables: Record<string, { role: string; dark: string; light: string }>;
};

function sources(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? sources(join(dir, e.name)) : /\.(css|ts)$/.test(e.name) ? [join(dir, e.name)] : [],
  );
}

test("the panel uses exactly the variables in theme.json", () => {
  const used = new Set<string>();
  for (const file of sources(join(root, "src"))) {
    for (const m of readFileSync(file, "utf8").matchAll(/var\((--vscode-[A-Za-z-]+)/g)) used.add(m[1]);
  }
  const listed = new Set(Object.keys(theme.variables));
  assert.deepEqual([...used].filter((v) => !listed.has(v)), [], "used but not in theme.json");
  assert.deepEqual([...listed].filter((v) => !used.has(v)), [], "in theme.json but not used");
});

function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: string, b: string): number {
  const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

test("every variable has a dark and a light value, and the defaults are readable", () => {
  for (const [name, v] of Object.entries(theme.variables)) {
    assert.ok(v.role && v.dark && v.light, name);
    if (!/font/.test(name)) {
      assert.match(v.dark, /^#[0-9a-f]{6}$/i, name);
      assert.match(v.light, /^#[0-9a-f]{6}$/i, name);
    }
  }
  const t = (name: string, mode: "dark" | "light") => theme.variables[name][mode];
  for (const mode of ["dark", "light"] as const) {
    for (const bg of ["--vscode-sideBar-background", "--vscode-editor-background"]) {
      // WCAG AA for normal text.
      assert.ok(contrast(t("--vscode-foreground", mode), t(bg, mode)) >= 4.5, `${mode} text on ${bg}`);
    }
    assert.ok(contrast(t("--vscode-button-foreground", mode), t("--vscode-button-background", mode)) >= 4.5, `${mode} button`);
    assert.ok(contrast(t("--vscode-input-foreground", mode), t("--vscode-input-background", mode)) >= 4.5, `${mode} input`);
  }
});
