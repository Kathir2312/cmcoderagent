using Cmcoder.Core;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>The host logic (H5-H18) with a fake IDE, against the real cmcoder: what the Visual Studio extension gets.</summary>
    public class HostTests
    {
        internal sealed class FakeIde : IIde
        {
            public readonly List<string> Panel = new List<string>();
            public readonly List<string> Navigator = new List<string>();
            public readonly List<string> Calls = new List<string>();
            public readonly List<Protocol.FileChange> Diffs = new List<Protocol.FileChange>();
            public readonly List<string> LogLines = new List<string>();
            public JObject? Context;

            public void ToPanel(string json) { lock (Panel) Panel.Add(json); }
            public void ToNavigator(string json) { lock (Navigator) Navigator.Add(json); }
            public void OpenNavigator() { lock (Calls) Calls.Add("openNavigator"); }
            public void FocusChat() { lock (Calls) Calls.Add("focusChat"); }
            public JObject? EditorContext() => Context;
            public bool AutoContext => true;
            public bool DiffReview => true;

            public void OpenDiff(string requestId, Protocol.FileChange change)
            {
                lock (Calls) Calls.Add("openDiff " + requestId);
                lock (Diffs) Diffs.Add(change);
            }

            public void CloseDiff(string requestId) { lock (Calls) Calls.Add("closeDiff " + requestId); }
            public IReadOnlyList<string> IdeTools => new[] { "getDiagnostics", "openFile" };

            public void RunIdeTool(string name, JObject input, Action<string, bool> done)
            {
                lock (Calls) Calls.Add("tool " + name);
                // Answered on another thread, as an IDE would from its UI thread.
                new Thread(() => done("app.py:1:1 warning: x is never used", false)).Start();
            }

            public void PickRewind(IReadOnlyList<Protocol.RewindPoint> points, Action<RewindChoice> chosen)
            {
                lock (Calls) Calls.Add("pickRewind " + points.Count);
                chosen(new RewindChoice(points[0].Turn, true, true, false));
            }

            public void AttachFile(Action<string> chosen) => chosen("src\\main.py");
            public void OpenExternal(string url) { lock (Calls) Calls.Add("open " + url); }
            public void Log(string line) { lock (LogLines) LogLines.Add(line); }

            public List<JObject> Sent(string kind)
            {
                lock (Panel) return Panel.Select(j => Json.ParseObject(j)!).Where(m => Json.Str(m, "kind") == kind).ToList();
            }

            public List<JObject> Events(string type) =>
                Sent("event").Select(m => Json.Obj(m, "event")!).Where(e => Json.Str(e, "type") == type).ToList();

            public string LogText()
            {
                lock (LogLines) return string.Join("\n", LogLines);
            }
        }

        private static Host.Config Config(string project, string url) => new Host.Config
        {
            ProgramSetting = Fixtures.Program(),
            ProjectDir = project,
            Client = "visualstudio",
            Product = "Acme Coder",
            Env = Fixtures.Env(url),
        };

        [SkippableFact]
        public void AWholeConversationThroughTheHost()
        {
            var script = "["
                + "{\"tool_calls\":[{\"name\":\"getDiagnostics\",\"arguments\":{}}]},"
                + "{\"content\":\"Checked the problems.\"},"
                + "{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"new.py\",\"content\":\"x = 2\\n\"}}]},"
                + "{\"content\":\"Wrote new.py.\"}]";
            using var server = new MockServer(script);
            var project = Fixtures.Project();
            var app = Path.Combine(project, "app.py");
            File.WriteAllText(app, "x = 1\n");
            var ide = new FakeIde
            {
                Context = EditorContext.Of(app, EditorContext.Selection(app, 1, 1, "x = 1"),
                    new[] { EditorContext.Diagnostic(app, 1, "warning", "x is never used", "lint") }),
            };
            var host = new Host(ide, Config(project, server.Url));

            // The page loads: cmcoder starts; the panel gets every event, then "ready".
            host.OnPanelMessage("{\"kind\":\"ready\"}");
            Fixtures.Until("ready", () => host.State == "ready", ide.LogText);
            Assert.Equal("starting", Json.Str(ide.Sent("state")[0], "state"));
            Assert.Single(ide.Events("system_init"));
            Assert.Equal("app.py:1 · 1 problem", Json.Str(ide.Sent("context").Last(), "label"));

            // 1. The model asks the IDE for problems; the context went with the message.
            host.OnPanelMessage("{\"kind\":\"send\",\"text\":\"any problems?\",\"includeContext\":true}");
            Fixtures.Until("the first turn", () => ide.Events("result").Count == 1, ide.LogText);
            Assert.Equal("Checked the problems.", Json.Str(ide.Events("result")[0], "result"));
            lock (ide.Calls) Assert.Contains("tool getDiagnostics", ide.Calls);
            Assert.Contains("x is never used", Json.Str(ide.Events("tool_result")[0], "content"));
            var first = server.Requests()[0].ToString();
            Assert.True(first.Contains("app.py") && first.Contains("x is never used"), "the editor context reached the model");

            // 2. An edit is shown in the IDE's diff viewer and accepted there.
            host.OnPanelMessage("{\"kind\":\"send\",\"text\":\"create new.py\",\"includeContext\":false}");
            Fixtures.Until("the diff", () => { lock (ide.Diffs) return ide.Diffs.Count > 0; }, ide.LogText);
            Protocol.FileChange change;
            lock (ide.Diffs) change = ide.Diffs[0];
            Assert.EndsWith("new.py", change.Path);
            Assert.Equal("x = 2\n", change.After);
            var id = Json.Str(ide.Events("permission_request")[0], "request_id")!;
            host.Answer(id, true, false, null); // the diff viewer's Accept
            Fixtures.Until("the second turn", () => ide.Events("result").Count == 2, ide.LogText);
            Assert.Equal("x = 2\n", File.ReadAllText(Path.Combine(project, "new.py")));
            lock (ide.Calls) Assert.Contains("closeDiff " + id, ide.Calls);
            Assert.Equal("Allowed", Json.Str(ide.Sent("permissionAnswered").Last(), "text"));
            var messages = Json.List(server.Requests()[2], "messages");
            var lastUser = messages.Last()["content"]!.ToString();
            Assert.Contains("create new.py", lastUser);
            Assert.DoesNotContain("x is never used", lastUser); // the panel's toggle was off

            // 3. The navigator, opened now, replays the turn.
            lock (ide.Navigator) ide.Navigator.Clear();
            host.OnNavigatorMessage("{\"kind\":\"ready\"}");
            JObject reset;
            lock (ide.Navigator) reset = Json.ParseObject(ide.Navigator[0])!;
            Assert.Equal("reset", Json.Str(reset, "kind"));
            Assert.Equal("create new.py", Json.Str(reset, "prompt"));
            Assert.Equal("qwen3-27b", Json.Str(reset, "model"));
            lock (ide.Navigator) Assert.Contains(ide.Navigator, m => m.Contains("\"tool_use\""));

            // 4. What the page can't make the IDE do.
            var resets = ide.Sent("reset").Count;
            host.OnPanelMessage("{\"kind\":\"resume\",\"id\":\"--permission-mode=bypassPermissions\"}");
            host.OnPanelMessage("{\"kind\":\"openLink\",\"href\":\"javascript:alert(1)\"}");
            host.OnPanelMessage("{\"kind\":\"openLink\",\"href\":\"file:///C:/Windows/win.ini\"}");
            host.OnPanelMessage("{\"kind\":\"openLink\",\"href\":\"https://example.com/docs\"}");
            host.OnPanelMessage("not json at all");
            host.OnPanelMessage("{\"kind\":\"setMode\",\"mode\":\"root\"}");
            Assert.Equal(resets, ide.Sent("reset").Count); // a bad session id doesn't restart anything
            lock (ide.Calls) Assert.Equal(new[] { "open https://example.com/docs" }, ide.Calls.Where(c => c.StartsWith("open ")).ToArray());
            Assert.True(host.Running);

            // 5. The @ button: a project path, with forward slashes.
            host.OnPanelMessage("{\"kind\":\"attachFile\"}");
            Assert.Equal("@src/main.py ", Json.Str(ide.Sent("prefill").Last(), "text"));

            // 6. Closing the solution stops cmcoder.
            host.Dispose();
            Assert.False(host.Running);
        }

        [Fact]
        public void ARelativeProgramSettingIsExplained()
        {
            var ide = new FakeIde();
            var host = new Host(ide, new Host.Config { ProgramSetting = Path.Combine("bin", "cmcoder"), ProjectDir = Path.GetTempPath() });
            host.OnPanelMessage("{\"kind\":\"ready\"}");
            var state = ide.Sent("state").Last();
            Assert.Equal("exited", Json.Str(state, "state"));
            Assert.Contains("full path", Json.Str(state, "message"));
            Assert.False(host.Running);
        }

        [Fact]
        public void NoProjectNoStart()
        {
            var ide = new FakeIde();
            var host = new Host(ide, new Host.Config());
            host.OnPanelMessage("{\"kind\":\"ready\"}");
            Assert.Contains("Open a solution or folder first", Json.Str(ide.Sent("state").Last(), "message"));
        }

        [SkippableFact]
        public void AFirstLineSentAtOnceIsNotMissed()
        {
            Skip.If(ProgramLocator.Windows, "uses a shell script as the program");
            var project = Fixtures.Project();
            var fast = Path.Combine(project, "fast-cmcoder");
            File.WriteAllText(fast, "#!/bin/sh\necho '{\"type\":\"system_init\",\"protocol_version\":1,\"session_id\":\"s\",\"cwd\":\".\","
                + "\"model\":\"m\",\"provider\":\"p\",\"tools\":[],\"permission_mode\":\"default\"}'\ncat > /dev/null\n");
            Fixtures.MakeExecutable(fast);
            for (var i = 0; i < 25; i++)
            {
                var ide = new FakeIde();
                var host = new Host(ide, new Host.Config { ProgramSetting = fast, ProjectDir = project });
                host.OnPanelMessage("{\"kind\":\"ready\"}");
                Fixtures.Until("run " + i + ": ready", () => host.State == "ready", ide.LogText, 10_000);
                host.Dispose();
            }
        }

        [SkippableFact]
        public void AProgramSpeakingAnotherProtocolIsStoppedWithAReason()
        {
            Skip.If(ProgramLocator.Windows, "uses a shell script as the program");
            var project = Fixtures.Project();
            var fake = Path.Combine(project, "old-cmcoder");
            File.WriteAllText(fake, "#!/bin/sh\necho '{\"type\":\"system_init\",\"protocol_version\":2,\"session_id\":\"s\",\"cwd\":\".\","
                + "\"model\":\"m\",\"provider\":\"p\",\"tools\":[],\"permission_mode\":\"default\"}'\ncat > /dev/null\n");
            Fixtures.MakeExecutable(fake);
            var ide = new FakeIde();
            var host = new Host(ide, new Host.Config { ProgramSetting = fake, ProjectDir = project });
            host.OnPanelMessage("{\"kind\":\"ready\"}");
            Fixtures.Until("the mismatch", () => ide.Sent("state").Any(s => (Json.Str(s, "message") ?? "").Contains("don't match")), ide.LogText);
            Fixtures.Until("it stopped", () => !host.Running, ide.LogText);
        }
    }
}
