// Proposed Write/Edit changes shown in VS Code's own diff editor, with
// Accept / Reject buttons in the editor's title bar.

import * as path from "node:path";
import * as vscode from "vscode";
import { productName } from "./brand";
import type { FileChange } from "./protocol";

export const SCHEME = "cmcoder-diff";

interface Review {
  change: FileChange;
  left: vscode.Uri;
  right: vscode.Uri;
}

/** Serves both sides of each diff as read-only virtual documents. */
export class DiffReview implements vscode.TextDocumentContentProvider {
  private readonly reviews = new Map<string, Review>();
  private readonly changed = new vscode.EventEmitter<vscode.Uri>();
  readonly onDidChange = this.changed.event;

  provideTextDocumentContent(uri: vscode.Uri): string {
    const { id, side } = parse(uri);
    const review = id ? this.reviews.get(id) : undefined;
    if (!review) return "";
    return side === "before" ? review.change.before ?? "" : review.change.after;
  }

  /** Opens the diff for a permission request. */
  async open(requestId: string, change: FileChange): Promise<void> {
    const name = path.basename(change.path);
    // The file name stays last in the path, so VS Code picks the right language.
    const uri = (side: string) =>
      vscode.Uri.from({ scheme: SCHEME, path: `/${side}/${name}`, query: `id=${requestId}&side=${side}` });
    const review = { change, left: uri("before"), right: uri("after") };
    this.reviews.set(requestId, review);
    const title = change.before === null ? `${name} (new file, proposed by ${productName()})` : `${name} ↔ proposed by ${productName()}`;
    await vscode.commands.executeCommand("vscode.diff", review.left, review.right, title, {
      preview: false,
      preserveFocus: true,
    });
  }

  has(requestId: string): boolean {
    return this.reviews.has(requestId);
  }

  /** The request whose diff is in the active editor, if any. */
  activeRequest(): string | undefined {
    const tab = vscode.window.tabGroups.activeTabGroup.activeTab;
    const input = tab?.input;
    if (input instanceof vscode.TabInputTextDiff && input.modified.scheme === SCHEME) {
      return parse(input.modified).id;
    }
    const uri = vscode.window.activeTextEditor?.document.uri;
    if (uri?.scheme === SCHEME) return parse(uri).id;
    // Not looking at a diff: fine as long as only one change is waiting.
    return this.reviews.size === 1 ? [...this.reviews.keys()][0] : undefined;
  }

  /** Closes the diff of a request (answered or cancelled). */
  async close(requestId: string): Promise<void> {
    if (!this.reviews.delete(requestId)) return;
    const tabs = vscode.window.tabGroups.all
      .flatMap((g) => g.tabs)
      .filter((t) => t.input instanceof vscode.TabInputTextDiff && parse(t.input.modified).id === requestId);
    if (tabs.length) await vscode.window.tabGroups.close(tabs, true);
  }

  async closeAll(): Promise<void> {
    await Promise.all([...this.reviews.keys()].map((id) => this.close(id)));
  }
}

function parse(uri: vscode.Uri): { id?: string; side?: string } {
  if (uri.scheme !== SCHEME) return {};
  const q = new URLSearchParams(uri.query);
  return { id: q.get("id") ?? undefined, side: q.get("side") ?? undefined };
}
