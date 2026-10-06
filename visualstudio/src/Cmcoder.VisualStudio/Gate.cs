#if CMCODER_GATE
using Cmcoder.Core;
using EnvDTE;
using EnvDTE80;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// The release gate inside a real Visual Studio (test builds only, never
    /// shipped): with CMCODER_VS_GATE set to a result file, it runs a whole
    /// conversation through the real extension, its real chat page (the panel's
    /// test driver), the bundled cmcoder and the mock model server (whose
    /// address comes in CMCODER_BASE_URL), writes what happened, and closes
    /// Visual Studio. visualstudio/gate/run-gate.ps1 sets it all up.
    /// </summary>
    internal static class Gate
    {
        private static readonly List<string> Steps = new List<string>();

        public static void StartIfAsked(CmcoderPackage package)
        {
            var result = Environment.GetEnvironmentVariable("CMCODER_VS_GATE");
            if (string.IsNullOrEmpty(result)) return;
            package.JoinableTaskFactory.RunAsync(async () =>
            {
                string? failure = null;
                try
                {
                    await RunAsync(package);
                }
                catch (Exception e)
                {
                    failure = e.ToString();
                    try
                    {
                        // What the chat page was doing, for the report.
                        await package.JoinableTaskFactory.SwitchToMainThreadAsync();
                        var chat = Session.Get().Chat;
                        failure += "\nchat page: " + (chat == null ? "not open" : await chat.EvaluateAsync(
                            "document.URL + ' ' + document.readyState + ' webview=' + typeof (window.chrome && window.chrome.webview) + ' scripts=' + Array.from(document.scripts).map(s => s.src).join(',') + ' styles=' + document.styleSheets.length + ' text=' + document.body.textContent.slice(0, 300)"));
                    }
                    catch (Exception probe)
                    {
                        failure += "\n(no page probe: " + probe.Message + ")";
                    }
                }
                await package.JoinableTaskFactory.SwitchToMainThreadAsync();
                var report = new JObject
                {
                    ["ok"] = failure == null,
                    ["failure"] = failure,
                    ["steps"] = new JArray(Steps),
                    ["state"] = Session.Get().Host.State + " " + Session.Get().LastState,
                    ["log"] = new JArray(Session.Get().Log.Recent()),
                };
                File.WriteAllText(result, report.ToString(Formatting.Indented));
                (Package.GetGlobalService(typeof(DTE)) as DTE2)?.Quit();
            });
        }

        /// <summary>Also to a file as it happens: if Visual Studio hangs, the runner shows how far it got.</summary>
        private static void Step(string what)
        {
            var line = DateTime.Now.ToString("HH:mm:ss") + " " + what;
            Steps.Add(line);
            try { File.AppendAllText(Environment.GetEnvironmentVariable("CMCODER_VS_GATE") + ".steps", line + Environment.NewLine); }
            catch (IOException) { }
        }

        private static async Task UntilAsync(string what, Func<Task<bool>> check, int seconds = 90)
        {
            var end = DateTime.UtcNow.AddSeconds(seconds);
            while (!await check())
            {
                if (DateTime.UtcNow > end) throw new Exception("timed out waiting for " + what);
                await Task.Delay(250);
            }
            Step(what);
        }

        private static Task UntilAsync(string what, Func<bool> check, int seconds = 90) => UntilAsync(what, () => Task.FromResult(check()), seconds);

        /// <summary>The panel's test driver: run(action, selector, value) → its JSON answer.</summary>
        private static async Task<string> DriverAsync(Panel panel, string action, string selector, string? value = null)
        {
            var script = "window.__cmcoderTest ? window.__cmcoderTest.run(" + JsonConvert.ToString(action) + "," + JsonConvert.ToString(selector)
                + "," + (value == null ? "undefined" : JsonConvert.ToString(value)) + ") : 'null'";
            var raw = await panel.EvaluateAsync(script);
            return JsonConvert.DeserializeObject<string>(raw) ?? "null";
        }

        private static async Task SendAsync(Panel panel, string text)
        {
            if (await DriverAsync(panel, "fill", "textarea", text) != "true") throw new Exception("couldn't type into the chat");
            if (await DriverAsync(panel, "press", "textarea", "Enter") != "true") throw new Exception("couldn't send");
        }

        private static async Task RunAsync(CmcoderPackage package)
        {
            await package.JoinableTaskFactory.SwitchToMainThreadAsync();
            var session = Session.Get();
            var dte = (DTE2)(await package.GetServiceAsync(typeof(DTE)))!;
            Step("loaded in Visual Studio " + dte.Version);

            // The folder Visual Studio was started with (Open Folder).
            string? folder = null;
            await UntilAsync("the folder is open", () => (folder = Session.SolutionFolder()) != null);
            var app = Path.Combine(folder!, "app.cs");

            // H7: an open file with a selection.
            VsShellUtilities.OpenDocument(package, app, Guid.Empty, out _, out _, out var appFrame, out _);
            appFrame.Show();
            await UntilAsync("app.cs is the active document", () => string.Equals(dte.ActiveDocument?.FullName, app, StringComparison.OrdinalIgnoreCase));
            ((TextSelection)dte.ActiveDocument.Selection).SelectLine();

            // H1, H2, H5: the chat opens, cmcoder (the bundled one) starts.
            var window = (ChatWindow)(await package.ShowToolWindowAsync(typeof(ChatWindow), 0, true, package.DisposalToken))!;
            var chat = window.Panel;
            await UntilAsync("cmcoder ready", () => session.Host.State == "ready" && session.Host.Running, 180);
            await UntilAsync("the page's test driver", async () => await DriverAsync(chat, "count", "textarea") == "1");
            await UntilAsync("the context label", async () => (await DriverAsync(chat, "text", ".context span")).Contains("app.cs:1"));
            await UntilAsync("code search's status item", () => window.CodeSearchText == "Code search: off");
            var diagnosticsText = session.Diagnostics(folder, dte.Version, true);
            if (!diagnosticsText.Contains("bin\\cmcoder\\cmcoder.exe")) throw new Exception("cmcoder didn't come from the extension:\n" + diagnosticsText);
            if (!diagnosticsText.Contains("cmcoder doctor --no-probe:")) throw new Exception("no doctor in the diagnostics");
            var key = Environment.GetEnvironmentVariable("CMCODER_API_KEY");
            if (!string.IsNullOrEmpty(key) && diagnosticsText.Contains(key)) throw new Exception("the API key is in the diagnostics");
            Step("diagnostics: the bundled program, doctor, no key");

            // H8: a message; the model asks Visual Studio for its problems.
            await SendAsync(chat, "any problems?");
            await UntilAsync("the first reply", async () => (await DriverAsync(chat, "texts", ".msg.assistant")).Contains("Checked the problems."));

            // H9, H10: a change in the diff window, accepted there.
            await SendAsync(chat, "create new.txt");
            await UntilAsync("the diff window", () => session.Ide.Diffs.OpenRequests.Count == 1);
            session.Ide.Diffs.Click(session.Ide.Diffs.OpenRequests.First(), DiffReview.Accept);
            await UntilAsync("new.txt written", () => File.Exists(Path.Combine(folder!, "new.txt")));
            await UntilAsync("the diff closed", () => session.Ide.Diffs.OpenRequests.Count == 0);
            await UntilAsync("the chat says Allowed", async () => (await DriverAsync(chat, "texts", ".permission .answer")).Contains("Allowed"));
            if (File.ReadAllText(Path.Combine(folder!, "new.txt")) != "x = 2\n") throw new Exception("new.txt has the wrong text");

            // Rejected there: nothing written.
            await SendAsync(chat, "create other.txt");
            await UntilAsync("the second diff window", () => session.Ide.Diffs.OpenRequests.Count == 1);
            session.Ide.Diffs.Click(session.Ide.Diffs.OpenRequests.First(), DiffReview.Reject);
            await UntilAsync("the chat says Denied", async () => (await DriverAsync(chat, "texts", ".permission .answer")).Contains("Denied"));
            await UntilAsync("the second diff closed", () => session.Ide.Diffs.OpenRequests.Count == 0);
            if (File.Exists(Path.Combine(folder!, "other.txt"))) throw new Exception("other.txt was written after Reject");

            // H15: the navigator, opened now, shows the turn.
            var navigator = (NavigatorWindow)(await package.ShowToolWindowAsync(typeof(NavigatorWindow), 0, true, package.DisposalToken))!;
            await UntilAsync("the navigator's turn", async () =>
                (await navigator.Panel.EvaluateAsync("document.body.textContent")).Contains("create other.txt"));

            // H19: the page can't navigate away.
            var url = chat.View.Source?.ToString();
            await chat.EvaluateAsync("location.href='https://example.com/'");
            await Task.Delay(1500);
            if (chat.View.Source?.ToString() != url) throw new Exception("the chat page navigated away to " + chat.View.Source);
            Step("the page stayed");

            // H14: Ask About Selection fills in a prompt.
            var commands = (System.ComponentModel.Design.IMenuCommandService)(await package.GetServiceAsync(typeof(System.ComponentModel.Design.IMenuCommandService)))!;
            commands.GlobalInvoke(new System.ComponentModel.Design.CommandID(Ids.CmdSet, Ids.AskAboutSelection));
            await UntilAsync("the prompt", async () =>
                (await chat.EvaluateAsync("document.querySelector('textarea').value")).Contains("Explain the selected code."));

            // H3: closing the folder stops cmcoder.
            dte.Solution.Close(false);
            await UntilAsync("cmcoder stopped", () => !session.Host.Running, 30);
        }
    }
}
#endif
