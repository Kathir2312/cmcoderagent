// The product's name, as packaged (packaging/vsix.py sets displayName from
// branding/brand.json). Commands, settings and the `cmcoder` program keep their names.

import * as vscode from "vscode";

let name = "cmcoder";

export function setBrand(extension: vscode.Extension<unknown>): void {
  const displayName = (extension.packageJSON as { displayName?: unknown }).displayName;
  if (typeof displayName === "string" && displayName.trim()) name = displayName.trim();
}

export function productName(): string {
  return name;
}
