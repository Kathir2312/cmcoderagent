using Cmcoder.Core;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// The extension's one conversation: the shared <see cref="Host"/> plus what
    /// Visual Studio does for it (<see cref="VsIde"/>). Every call into Host runs
    /// on one background thread, so the UI thread never waits for cmcoder, and
    /// Host may wait for the UI thread (the editor context) without a deadlock.
    /// </summary>
    internal sealed class Session
    {
        private static Session? current;
        private static readonly object CurrentLock = new object();

        private readonly BlockingCollection<Action> work = new BlockingCollection<Action>();
        private readonly Thread worker;
        private readonly Host.Config config = new Host.Config { Client = "visualstudio" };
        private readonly List<Action> codeSearchViews = new List<Action>();

        private Session()
        {
            config.Product = Brand.Product;
            config.ExtensionDir = Brand.ExtensionDir;
            Log = new OutputLog();
            Ide = new VsIde(this);
            Host = new Host(Ide, config);
            CodeSearch = new CodeSearch(Host, _ => { lock (codeSearchViews) foreach (var v in codeSearchViews) v(); });
            worker = new Thread(() =>
            {
                foreach (var action in work.GetConsumingEnumerable())
                {
                    try { action(); }
                    catch (Exception e) { Log.Line(Brand.Product + ": " + e); }
                }
            }) { IsBackground = true, Name = "cmcoder session" };
            worker.Start();
        }

        public static Session Get()
        {
            lock (CurrentLock) return current ??= new Session();
        }

        /// <summary>Visual Studio closes: end cmcoder (H3).</summary>
        public static void DisposeAll()
        {
            Session? s;
            lock (CurrentLock)
            {
                s = current;
                current = null;
            }
            if (s == null) return;
            s.work.CompleteAdding();
            // Not on the UI thread: the session's thread may hold the host while it
            // waits for the UI thread. If it doesn't finish in time, the job object
            // still ends cmcoder when Visual Studio exits.
            System.Threading.Tasks.Task.Run(() => s.Host.Dispose()).Wait(TimeSpan.FromSeconds(8));
        }

        public Host Host { get; }
        public CodeSearch CodeSearch { get; }
        public OutputLog Log { get; }
        internal VsIde Ide { get; }
        internal volatile Panel? Chat;

        /// <summary>The last state sent to the chat, with its message (diagnostics).</summary>
        internal volatile string LastState = "";
        internal volatile Panel? Navigator;

        /// <summary>Runs on the session's thread, after reading the settings cmcoder starts with again.</summary>
        public void Run(Action<Host> action)
        {
            if (work.IsAddingCompleted) return;
            try
            {
                work.Add(() =>
                {
                    RefreshConfig();
                    action(Host);
                });
            }
            catch (InvalidOperationException)
            {
                // closing down
            }
        }

        /// <summary>Settings and the solution folder, read again before anything that may start cmcoder (H17).</summary>
        private void RefreshConfig()
        {
            ThreadHelper.JoinableTaskFactory.Run(async () =>
            {
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                var options = CmcoderPackage.Instance?.Options;
                if (options != null)
                {
                    config.ProgramSetting = string.IsNullOrWhiteSpace(options.Program) ? null : options.Program.Trim();
                    config.PermissionMode = options.PermissionModeArgument;
                    config.TrustProject = options.TrustProject;
                }
                config.ProjectDir = SolutionFolder();
            });
        }

        /// <summary>The open solution's folder, or the folder opened with File > Open > Folder (UI thread).</summary>
        internal static string? SolutionFolder()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (Package.GetGlobalService(typeof(SVsSolution)) is not IVsSolution solution) return null;
            if (solution.GetSolutionInfo(out var dir, out _, out _) != 0 || string.IsNullOrEmpty(dir)) return null;
            return Directory.Exists(dir) ? dir.TrimEnd('\\') : null;
        }

        /// <summary>Tests: extra environment for cmcoder.</summary>
        internal void TestEnvironment(IDictionary<string, string?> env) => config.Env = env;

        public void SolutionOpened()
        {
            if (Chat != null) Run(h => h.NewConversation(new string[0]));
        }

        public void ChatOpened(Panel panel) => Chat = panel;

        /// <summary>The chat closed: cmcoder stops with it (no hidden conversation keeps running).</summary>
        public void ChatClosed(Panel panel)
        {
            if (Chat != panel) return;
            Chat = null;
            Run(h => h.Dispose());
        }

        public void NavigatorOpened(Panel panel) => Navigator = panel;

        public void NavigatorClosed(Panel panel)
        {
            if (Navigator == panel) Navigator = null;
        }

        /// <summary>changed runs (any thread) when code search's state changes; dispose to stop.</summary>
        public IDisposable OnCodeSearchChange(Action changed)
        {
            lock (codeSearchViews) codeSearchViews.Add(changed);
            return new Unsubscribe(() => { lock (codeSearchViews) codeSearchViews.Remove(changed); });
        }

        private sealed class Unsubscribe : IDisposable
        {
            private readonly Action action;
            public Unsubscribe(Action action) => this.action = action;
            public void Dispose() => action();
        }

        /// <summary>The diff window's buttons (H10).</summary>
        public void Answer(string requestId, bool allow, bool remember) => Run(h => h.Answer(requestId, allow, remember, null));

        // -- Copy Diagnostics (H22) --------------------------------------------------------------

        /// <summary>What support needs, to the clipboard (nothing is sent anywhere); cmcoder runs in the background.</summary>
        public void CopyDiagnostics()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var project = SolutionFolder();
            var vs = (Package.GetGlobalService(typeof(EnvDTE.DTE)) as EnvDTE.DTE)?.Version ?? "?";
            System.Threading.Tasks.Task.Run(() =>
            {
                var text = Diagnostics(project, vs, true);
                Ui.Later(async () =>
                {
                    await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                    System.Windows.Clipboard.SetText(text);
                    VsShellUtilities.ShowMessageBox(CmcoderPackage.Instance!, "The diagnostics are on the clipboard. Read them before you share them.",
                        Brand.Product, OLEMSGICON.OLEMSGICON_INFO, OLEMSGBUTTON.OLEMSGBUTTON_OK, OLEMSGDEFBUTTON.OLEMSGDEFBUTTON_FIRST);
                });
            });
        }

        internal string Diagnostics(string? project, string vsVersion, bool runProgram)
        {
            var s = new StringBuilder();
            s.Append(Brand.Product).Append(" for Visual Studio ").Append(typeof(Session).Assembly.GetName().Version).Append('\n');
            s.Append("Visual Studio: ").Append(vsVersion).Append('\n');
            s.Append("OS: ").Append(Environment.OSVersion).Append(Environment.Is64BitOperatingSystem ? " 64-bit" : "").Append('\n');
            var env = Environment.GetEnvironmentVariables().Cast<System.Collections.DictionaryEntry>()
                .ToDictionary(e => (string)e.Key, e => (string?)e.Value, StringComparer.OrdinalIgnoreCase);
            var found = ProgramLocator.Find(config.ProgramSetting, Brand.ExtensionDir, project, env);
            s.Append("Program: ").Append(found.Program ?? "not found: " + found.Problem).Append('\n');
            if (runProgram && found.Program != null)
            {
                s.Append("Version: ").Append(Output(found.Program, project, "--version").Trim()).Append('\n');
                s.Append("\ncmcoder doctor --no-probe:\n").Append(Output(found.Program, project, "doctor", "--no-probe")).Append('\n');
            }
            s.Append("State: ").Append(Host.State).Append(' ').Append(LastState).Append('\n');
            s.Append("\nRecent log:\n");
            foreach (var line in Log.Recent()) s.Append(line).Append('\n');
            return s.ToString();
        }

        /// <summary>What the program prints, at most 30 s; no shell, no window.</summary>
        private static string Output(string program, string? project, params string[] args)
        {
            try
            {
                var psi = new ProcessStartInfo(program, AgentProcess.CommandLine(args))
                {
                    UseShellExecute = false,
                    CreateNoWindow = true,
                    RedirectStandardOutput = true,
                    RedirectStandardError = true,
                    RedirectStandardInput = true,
                    StandardOutputEncoding = Encoding.UTF8,
                    StandardErrorEncoding = Encoding.UTF8,
                };
                if (project != null) psi.WorkingDirectory = project;
                psi.EnvironmentVariables["NO_COLOR"] = "1";
                using var p = Process.Start(psi)!;
                p.StandardInput.Close();
                var err = p.StandardError.ReadToEndAsync();
                var output = p.StandardOutput.ReadToEndAsync();
                if (!p.WaitForExit(30_000))
                {
                    try { p.Kill(); } catch (Exception) { }
                    return "(no answer in 30 s)";
                }
                return output.Result + err.Result;
            }
            catch (Exception e)
            {
                return "(could not run it: " + e.Message + ")";
            }
        }
    }

    /// <summary>The extension's log: an Output window pane, and the last 300 lines for diagnostics.</summary>
    internal sealed class OutputLog
    {
        private static readonly Guid PaneId = new Guid("7d1c9a3e-2b4f-4c8e-a6d5-9e1f3b7c2a48");
        private readonly LinkedList<string> recent = new LinkedList<string>();
        private IVsOutputWindowPane? pane;

        public void Line(string line)
        {
            lock (recent)
            {
                recent.AddLast(line);
                while (recent.Count > 300) recent.RemoveFirst();
            }
#if CMCODER_GATE
            // The gate's log as it happens (a hang leaves no report).
            var gate = Environment.GetEnvironmentVariable("CMCODER_VS_GATE");
            if (!string.IsNullOrEmpty(gate))
            {
                try { lock (recent) File.AppendAllText(gate + ".log", DateTime.Now.ToString("HH:mm:ss ") + line + Environment.NewLine); }
                catch (IOException) { }
            }
#endif
            Ui.Later(async () =>
            {
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                Pane()?.OutputStringThreadSafe(line + Environment.NewLine);
            });
        }

        public List<string> Recent()
        {
            lock (recent) return recent.ToList();
        }

        public void Show()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            Pane()?.Activate();
            (Package.GetGlobalService(typeof(EnvDTE.DTE)) as EnvDTE.DTE)?.ExecuteCommand("View.Output");
        }

        private IVsOutputWindowPane? Pane()
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (pane != null) return pane;
            if (Package.GetGlobalService(typeof(SVsOutputWindow)) is not IVsOutputWindow output) return null;
            var id = PaneId;
            output.CreatePane(ref id, Brand.Product, 1, 0);
            output.GetPane(ref id, out pane);
            return pane;
        }
    }
}
