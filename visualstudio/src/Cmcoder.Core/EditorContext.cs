using Newtonsoft.Json.Linq;
using System.Collections.Generic;

namespace Cmcoder.Core
{
    /// <summary>
    /// What the user has open (H7), in the shape cmcoder expects with a message:
    /// the active file, the selection (lines from 1) and its errors and
    /// warnings (at most <see cref="MaxDiagnostics"/>).
    /// </summary>
    public static class EditorContext
    {
        public const int MaxDiagnostics = 30;

        public static JObject Selection(string path, int startLine, int endLine, string text) => new JObject
        {
            ["path"] = path,
            ["start_line"] = startLine,
            ["end_line"] = System.Math.Max(startLine, endLine),
            ["text"] = text,
        };

        /// <summary>severity: "error", "warning", "info" or "hint".</summary>
        public static JObject Diagnostic(string path, int line, string severity, string message, string? source) => new JObject
        {
            ["path"] = path,
            ["line"] = line,
            ["severity"] = severity,
            ["message"] = message,
            ["source"] = source == null ? JValue.CreateNull() : (JToken)source,
        };

        /// <summary>The context for a message: null when no file is open. Only errors and warnings, at most 30.</summary>
        public static JObject? Of(string? activeFile, JObject? selection, IEnumerable<JObject> diagnostics)
        {
            if (activeFile == null) return null;
            var kept = new JArray();
            foreach (var d in diagnostics)
            {
                var severity = Json.Str(d, "severity");
                if ((severity == "error" || severity == "warning") && kept.Count < MaxDiagnostics) kept.Add(d);
            }
            return new JObject
            {
                ["active_file"] = activeFile,
                ["selection"] = selection ?? (JToken)JValue.CreateNull(),
                ["diagnostics"] = kept,
            };
        }

        /// <summary>A selection's last line, from 1: a selection ending at column 0 doesn't include that line.</summary>
        public static int EndLine(int startLine0, int endLine0, int endColumn0) =>
            endColumn0 == 0 && endLine0 > startLine0 ? endLine0 : endLine0 + 1;

        /// <summary>The chat's short label, e.g. "app.py:10-14 · 2 problems"; null without a file.</summary>
        public static string? Label(JObject? context)
        {
            var file = Json.Str(context, "active_file");
            if (file == null) return null;
            var label = new System.Text.StringBuilder(System.IO.Path.GetFileName(file));
            var sel = Json.Obj(context, "selection");
            if (sel != null)
            {
                var start = Json.Num(sel, "start_line", 0);
                var end = Json.Num(sel, "end_line", start);
                label.Append(':').Append(start);
                if (end > start) label.Append('-').Append(end);
            }
            var n = Json.List(context, "diagnostics").Count;
            if (n > 0) label.Append(" · ").Append(n).Append(n == 1 ? " problem" : " problems");
            return label.ToString();
        }
    }
}
