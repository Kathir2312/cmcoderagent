using Cmcoder.Core;
using EnvDTE;
using EnvDTE80;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using Microsoft.VisualStudio.TextManager.Interop;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// What Visual Studio does for the host (<see cref="IIde"/>): the editor
    /// context and the Error List (H7, H8), the diff window (H9), the pickers
    /// (H11, H13), links (H18) and the log. Called from the session's thread;
    /// UI work moves to the UI thread.
    /// </summary>
    internal sealed class VsIde : IIde
    {
        private static readonly string[] Tools = { "getDiagnostics", "openFile" };
        private const int MaxToolLines = 200;
        private readonly Session session;
        private readonly DiffReview diffs;

        public VsIde(Session session)
        {
            this.session = session;
            diffs = new DiffReview(session);
        }

        internal DiffReview Diffs => diffs;

        private static void Later(Action action)
        {
            Cmcoder.VisualStudio.Ui.Later(async () =>
            {
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                try { action(); }
                catch (Exception e) { Session.Get().Log.Line(Brand.Product + ": " + e); }
            });
        }

        private static T OnUi<T>(Func<T> read) => ThreadHelper.JoinableTaskFactory.Run(async () =>
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
            return read();
        });

        private static DTE2? Dte()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            return Package.GetGlobalService(typeof(DTE)) as DTE2;
        }

        public void ToPanel(string json) => session.Chat?.Post(json);

        public void ToNavigator(string json) => session.Navigator?.Post(json);

        public void OpenNavigator() => Later(() => CmcoderPackage.Instance?.ShowNavigator());

        public void FocusChat() => Later(() => CmcoderPackage.Instance?.ShowChat(true));

        public bool AutoContext => OnUi(() => CmcoderPackage.Instance?.Options.AutoContext ?? true);

        public bool DiffReview => OnUi(() => CmcoderPackage.Instance?.Options.DiffReview ?? true);

        public IReadOnlyList<string> IdeTools => Tools;

        // -- the editor context (H7) ------------------------------------------------------------

        public JObject? EditorContext() => OnUi(ReadContext);

        private static JObject? ReadContext()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var doc = Dte()?.ActiveDocument;
            var path = doc?.FullName;
            if (string.IsNullOrEmpty(path) || !File.Exists(path)) return null;
            JObject? selection = null;
            if (doc!.Selection is TextSelection sel && !sel.IsEmpty)
            {
                var top = sel.TopPoint;
                var bottom = sel.BottomPoint;
                // Lines and columns from 1 in DTE; EndLine wants them from 0.
                var end = Core.EditorContext.EndLine(top.Line - 1, bottom.Line - 1, bottom.LineCharOffset - 1);
                selection = Core.EditorContext.Selection(path!, top.Line, end, sel.Text);
            }
            var diagnostics = Problems(path).Select(p => Core.EditorContext.Diagnostic(p.File, p.Line, p.Severity, p.Message, null));
            return Core.EditorContext.Of(path, selection, diagnostics);
        }

        private sealed class Problem
        {
            public string File = "";
            public int Line;
            public int Column;
            public string Severity = "info";
            public string Message = "";
        }

        /// <summary>The Error List's entries (one file, or all). UI thread.</summary>
        private static List<Problem> Problems(string? file)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var list = new List<Problem>();
            var items = Dte()?.ToolWindows.ErrorList.ErrorItems;
            if (items == null) return list;
            for (var i = 1; i <= items.Count; i++)
            {
                ErrorItem item;
                try { item = items.Item(i); } catch (ArgumentException) { continue; }
                var name = item.FileName ?? "";
                if (file != null && !string.Equals(name, file, StringComparison.OrdinalIgnoreCase)) continue;
                list.Add(new Problem
                {
                    File = name,
                    Line = Math.Max(1, item.Line),
                    Column = Math.Max(1, item.Column),
                    Severity = item.ErrorLevel switch
                    {
                        vsBuildErrorLevel.vsBuildErrorLevelHigh => "error",
                        vsBuildErrorLevel.vsBuildErrorLevelMedium => "warning",
                        _ => "info",
                    },
                    Message = item.Description ?? "",
                });
            }
            return list;
        }

        // -- IDE tools (H8) -----------------------------------------------------------------------

        public void RunIdeTool(string name, JObject input, Action<string, bool> done)
        {
            var file = Json.Str(input, "file_path");
            switch (name)
            {
                case "getDiagnostics":
                    Later(() => done(Diagnostics(file), false));
                    return;
                case "openFile":
                    if (file == null)
                    {
                        done("file_path is required.", true);
                        return;
                    }
                    var line = Json.Num(input, "line", -1);
                    Later(() => OpenFile(file, line, done));
                    return;
                default:
                    done("Unknown IDE tool " + name + ".", true);
                    return;
            }
        }

        /// <summary>"path:line:col severity: message", paths relative to the solution folder.</summary>
        private static string Diagnostics(string? file)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var root = Session.SolutionFolder();
            var problems = Problems(file == null ? null : Path.GetFullPath(file));
            if (problems.Count == 0) return file != null ? "No problems in this file." : "No problems.";
            var lines = problems.Take(MaxToolLines).Select(p =>
                Relative(root, p.File) + ":" + p.Line + ":" + p.Column + " " + p.Severity + ": " + p.Message).ToList();
            var more = problems.Count > lines.Count ? "\n… " + (problems.Count - lines.Count) + " more" : "";
            return string.Join("\n", lines) + more;
        }

        private static string Relative(string? root, string path)
        {
            if (root != null && path.StartsWith(root + "\\", StringComparison.OrdinalIgnoreCase))
                path = path.Substring(root.Length + 1);
            return path.Replace('\\', '/');
        }

        private static void OpenFile(string file, long line, Action<string, bool> done)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            try
            {
                VsShellUtilities.OpenDocument(CmcoderPackage.Instance!, file, Guid.Empty, out _, out _, out var frame, out var view);
                frame?.Show();
                if (view != null && line > 0)
                {
                    view.GetBuffer(out var buffer);
                    var last = 0;
                    buffer?.GetLineCount(out last);
                    var target = (int)Math.Min(line - 1, Math.Max(0, last - 1));
                    view.SetCaretPos(target, 0);
                    view.CenterLines(target, 1);
                }
                done("Opened " + Relative(Session.SolutionFolder(), file) + " in the editor.", false);
            }
            catch (Exception e)
            {
                done("Could not open " + file + ": " + e.Message, true);
            }
        }

        // -- diffs, pickers, links, log -------------------------------------------------------------

        public void OpenDiff(string requestId, Protocol.FileChange change) => Later(() => diffs.Open(requestId, change));

        public void CloseDiff(string requestId) => Later(() => diffs.Close(requestId));

        public void PickRewind(IReadOnlyList<Protocol.RewindPoint> points, Action<RewindChoice> chosen) =>
            Later(() => Dialogs.PickRewind(points, c => session.Run(_ => chosen(c))));

        public void AttachFile(Action<string> chosen) =>
            Later(() => Dialogs.AttachFile(Session.SolutionFolder(), path => session.Run(_ => chosen(path))));

        public void OpenExternal(string url) => Later(() => System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo(url) { UseShellExecute = true }));

        public void Log(string line) => session.Log.Line(line);
    }
}
