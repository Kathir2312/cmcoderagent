// What the user has open in the editor, sent with each message (item 7),
// and the IDE tools cmcoder can ask the editor to run (item 8).

import * as path from "node:path";
import * as vscode from "vscode";
import type { IdeContext, IdeDiagnostic } from "./protocol";

const MAX_DIAGNOSTICS = 30;

/** The editor the user last worked in (the chat panel itself has focus while typing). */
export function currentEditor(): vscode.TextEditor | undefined {
  const editor = vscode.window.activeTextEditor ?? vscode.window.visibleTextEditors[0];
  return editor && editor.document.uri.scheme === "file" ? editor : undefined;
}

export function editorContext(editor = currentEditor()): IdeContext | null {
  if (!editor) return null;
  const doc = editor.document;
  const sel = editor.selection;
  const selection = sel.isEmpty
    ? null
    : {
        path: doc.uri.fsPath,
        start_line: sel.start.line + 1,
        // A selection ending at column 0 doesn't include that line.
        end_line: sel.end.character === 0 && sel.end.line > sel.start.line ? sel.end.line : sel.end.line + 1,
        text: doc.getText(sel),
      };
  const diagnostics = toIde(doc.uri, vscode.languages.getDiagnostics(doc.uri)).filter(
    (d) => d.severity === "error" || d.severity === "warning",
  );
  return { active_file: doc.uri.fsPath, selection, diagnostics: diagnostics.slice(0, MAX_DIAGNOSTICS) };
}

/** A short label for the chat panel, e.g. "app.py:10-14 · 2 problems". */
export function contextLabel(context: IdeContext | null): string | null {
  if (!context?.active_file) return null;
  let label = path.basename(context.active_file);
  const sel = context.selection;
  if (sel) label += sel.end_line > sel.start_line ? `:${sel.start_line}-${sel.end_line}` : `:${sel.start_line}`;
  const n = context.diagnostics?.length ?? 0;
  if (n) label += ` · ${n} problem${n === 1 ? "" : "s"}`;
  return label;
}

function severity(s: vscode.DiagnosticSeverity): IdeDiagnostic["severity"] {
  switch (s) {
    case vscode.DiagnosticSeverity.Error:
      return "error";
    case vscode.DiagnosticSeverity.Warning:
      return "warning";
    case vscode.DiagnosticSeverity.Information:
      return "info";
    default:
      return "hint";
  }
}

function toIde(uri: vscode.Uri, diags: readonly vscode.Diagnostic[]): IdeDiagnostic[] {
  return diags.map((d) => ({
    path: uri.fsPath,
    line: d.range.start.line + 1,
    severity: severity(d.severity),
    message: d.message,
    source: d.source ?? null,
  }));
}

// --- IDE tools -----------------------------------------------------------------

export const IDE_TOOLS = ["getDiagnostics", "openFile"];

export async function runIdeTool(
  name: string,
  input: Record<string, unknown>,
): Promise<{ content: string; is_error: boolean }> {
  const file = typeof input.file_path === "string" ? input.file_path : undefined;
  switch (name) {
    case "getDiagnostics": {
      const entries: Array<[vscode.Uri, readonly vscode.Diagnostic[]]> = file
        ? [[vscode.Uri.file(file), vscode.languages.getDiagnostics(vscode.Uri.file(file))]]
        : vscode.languages.getDiagnostics();
      const lines = entries.flatMap(([uri, diags]) =>
        diags.map((d) => {
          const where = `${vscode.workspace.asRelativePath(uri).replace(/\\/g, "/")}:${d.range.start.line + 1}:${d.range.start.character + 1}`;
          return `${where} ${severity(d.severity)}: ${d.message}${d.source ? ` (${d.source})` : ""}`;
        }),
      );
      if (!lines.length) return { content: file ? "No problems in this file." : "No problems.", is_error: false };
      const shown = lines.slice(0, 200);
      const more = lines.length > shown.length ? `\n… ${lines.length - shown.length} more` : "";
      return { content: shown.join("\n") + more, is_error: false };
    }
    case "openFile": {
      if (!file) return { content: "file_path is required.", is_error: true };
      try {
        const doc = await vscode.workspace.openTextDocument(vscode.Uri.file(file));
        const line = typeof input.line === "number" ? Math.max(0, input.line - 1) : undefined;
        const editor = await vscode.window.showTextDocument(doc, { preview: false, preserveFocus: true });
        if (line !== undefined) {
          const pos = new vscode.Position(Math.min(line, doc.lineCount - 1), 0);
          editor.selection = new vscode.Selection(pos, pos);
          editor.revealRange(new vscode.Range(pos, pos), vscode.TextEditorRevealType.InCenter);
        }
        return { content: `Opened ${vscode.workspace.asRelativePath(doc.uri)} in the editor.`, is_error: false };
      } catch (e) {
        return { content: `Could not open ${file}: ${(e as Error).message}`, is_error: true };
      }
    }
    default:
      return { content: `Unknown IDE tool ${name}.`, is_error: true };
  }
}
