using Cmcoder.Core;
using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>H2-H4 against the real cmcoder and the mock model server (as the JVM core's AgentProcessTest).</summary>
    public class AgentProcessTests
    {
        internal sealed class Recorder : AgentProcess.IListener
        {
            public readonly BlockingCollection<Protocol.Event> Events = new BlockingCollection<Protocol.Event>();
            public readonly List<string> Log = new List<string>();
            public int? Code;
            public bool? Expected;
            public string? Error;

            public void OnEvent(Protocol.Event e) => Events.Add(e);

            public void OnLog(string line)
            {
                lock (Log) Log.Add(line);
            }

            public void OnExit(int? code, bool expected, string? error)
            {
                Code = code;
                Expected = expected;
                Error = error;
            }

            public Protocol.Event Next(string type)
            {
                var end = DateTime.UtcNow.AddSeconds(60);
                while (DateTime.UtcNow < end)
                    if (Events.TryTake(out var e, 1000) && e.Type == type) return e;
                lock (Log) throw new Xunit.Sdk.XunitException("no " + type + " event; log: " + string.Join("\n", Log));
            }
        }

        [SkippableFact]
        public void ATurnWithAPermissionPromptAllowedThenDenied()
        {
            var script = "[{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"hello.py\",\"content\":\"print('hi')\\n\"}}]},"
                + "{\"content\":\"Created hello.py.\"},"
                + "{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"nope.py\",\"content\":\"x\"}}]},"
                + "{\"content\":\"OK, I won't.\"}]";
            using var server = new MockServer(script);
            var cwd = Fixtures.Project();
            var r = new Recorder();
            var agent = AgentProcess.Start(Fixtures.Program(), new List<string>(), cwd, Fixtures.Env(server.Url), r);
            Assert.Equal(1, Json.Num(r.Next("system_init").Fields, "protocol_version", -1));

            Assert.True(agent.Send(Protocol.UserMessage("create hello.py", null)));
            var ask = r.Next("permission_request");
            Assert.Equal("Write", ask.Str("name"));
            var change = Protocol.FileChange.Of(ask.Obj("change"))!;
            Assert.Null(change.Before); // a new file
            Assert.Equal("print('hi')\n", change.After);
            agent.Send(Protocol.PermissionResponse(ask.Str("request_id")!, true, false, null));
            Assert.Equal("Created hello.py.", r.Next("result").Str("result"));
            Assert.Equal("print('hi')\n", File.ReadAllText(Path.Combine(cwd, "hello.py")));

            agent.Send(Protocol.UserMessage("create nope.py", null));
            var ask2 = r.Next("permission_request");
            agent.Send(Protocol.PermissionResponse(ask2.Str("request_id")!, false, false, "don't"));
            Assert.Equal("OK, I won't.", r.Next("result").Str("result"));
            Assert.False(File.Exists(Path.Combine(cwd, "nope.py")));

            agent.Stop();
            Assert.True(agent.WaitForExit(10_000));
            Assert.Equal(0, r.Code);
            Assert.True(r.Expected);
            Assert.Null(r.Error);
            Assert.False(agent.Send(Protocol.Interrupt())); // gone: sending reports it
        }

        [SkippableFact]
        public void TextOutsideAsciiArrivesIntact()
        {
            var text = "Développement ä ✓ 😀 中文 — fin";
            using var server = new MockServer("[{\"content\":" + Json.Write(text) + "}]");
            var r = new Recorder();
            var agent = AgentProcess.Start(Fixtures.Program(), new List<string>(), Fixtures.Project(), Fixtures.Env(server.Url), r);
            r.Next("system_init");
            agent.Send(Protocol.UserMessage("é?", null));
            Assert.Equal(text, r.Next("result").Str("result"));
            Assert.Contains("é?", server.Requests()[0].ToString());
            agent.Stop();
        }

        [Fact]
        public void AMissingProgramIsReportedNotThrown()
        {
            var r = new Recorder();
            var missing = Path.Combine(Path.GetTempPath(), "no-such-cmcoder" + (ProgramLocator.Windows ? ".exe" : ""));
            var agent = AgentProcess.Start(missing, new List<string>(), Path.GetTempPath(), new Dictionary<string, string?>(), r);
            Assert.True(agent.WaitForExit(5_000));
            Assert.False(r.Expected);
            Assert.Contains("not found", r.Error);
            Assert.False(agent.Running);
            Assert.False(agent.Send(Protocol.Interrupt()));
            agent.Stop(); // harmless
        }

        [SkippableFact]
        public void ACrashIsReportedAsUnexpected()
        {
            var r = new Recorder();
            var agent = AgentProcess.Start(Fixtures.Program(), new List<string> { "--no-such-option" }, Fixtures.Project(), new Dictionary<string, string?>(), r);
            Assert.True(agent.WaitForExit(60_000));
            Assert.False(r.Expected);
            Assert.Equal(2, r.Code);
            Assert.Null(r.Error);
            lock (r.Log) Assert.Contains("no-such-option", string.Join("\n", r.Log));
        }

        [SkippableFact]
        public void StoppingEndsEverythingItStarted()
        {
            // A shell command still running when the solution closes (H3, gate G20).
            var command = ProgramLocator.Windows ? "ping -n 120 127.0.0.1" : "sleep 120";
            var script = "[{\"tool_calls\":[{\"name\":\"Bash\",\"arguments\":{\"command\":" + Json.Write(command) + "}}]},{\"content\":\"done\"}]";
            using var server = new MockServer(script);
            var r = new Recorder();
            var agent = AgentProcess.Start(Fixtures.Program(), new List<string> { "--permission-mode", "bypassPermissions" },
                Fixtures.Project(), Fixtures.Env(server.Url), r);
            r.Next("system_init");
            agent.Send(Protocol.UserMessage("wait a bit", null));
            Assert.Equal("Bash", r.Next("tool_use").Str("name"));
            var marker = ProgramLocator.Windows ? "ping" : "sleep";
            List<int> children = new List<int>();
            Fixtures.Until("the shell command", () => (children = Processes.Under(agent.Id!.Value)).Any(pid => Processes.Name(pid).Contains(marker)),
                () => string.Join(", ", children.Select(Processes.Name)), 30_000);
            agent.Stop(3_000);
            Assert.True(agent.WaitForExit(15_000));
            Fixtures.Until("everything it started to end", () => !children.Any(Processes.Alive),
                () => "still running: " + string.Join(", ", children.Where(Processes.Alive).Select(Processes.Name)), 15_000);
        }

        [Fact]
        public void LinesThatArentEventsGoToTheLog()
        {
            Skip.If(ProgramLocator.Windows, "uses a shell script as the program");
            var fake = Path.Combine(Fixtures.Project(), "fake-cmcoder");
            File.WriteAllText(fake, "#!/bin/sh\necho 'not json'\necho '[1]'\necho '{\"type\":\"warning\",\"message\":\"w\"}'\necho oops >&2\nprintf '\\033[1m--bold\\033[0m\\n' >&2\nexit 0\n");
            Fixtures.MakeExecutable(fake);
            var r = new Recorder();
            var agent = AgentProcess.Start(fake, new List<string>(), Path.GetTempPath(), new Dictionary<string, string?>(), r);
            Assert.True(agent.WaitForExit(10_000));
            Assert.Equal("warning", r.Next("warning").Type);
            lock (r.Log)
            {
                Assert.Contains("[stdout] not json", r.Log);
                Assert.Contains("[stdout] [1]", r.Log);
                Assert.Contains("oops", r.Log);
                Assert.Contains("--bold", r.Log); // terminal formatting removed
            }
            Assert.False(r.Expected); // it ended by itself
        }
    }

    /// <summary>Processes, for checking nothing is left running.</summary>
    internal static class Processes
    {
        public static List<int> Under(int pid)
        {
            if (!ProgramLocator.Windows) return AgentProcess.Descendants(pid);
            // Windows: parent ids from WMI, through PowerShell (on every Windows runner).
            var psi = new ProcessStartInfo("powershell", "-NoProfile -Command \"Get-CimInstance Win32_Process | ForEach-Object { \\\"$($_.ProcessId) $($_.ParentProcessId)\\\" }\"")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true,
            };
            using var p = Process.Start(psi)!;
            var parents = new Dictionary<int, int>();
            foreach (var line in p.StandardOutput.ReadToEnd().Split('\n'))
            {
                var parts = line.Trim().Split(' ');
                if (parts.Length == 2 && int.TryParse(parts[0], out var id) && int.TryParse(parts[1], out var parent)) parents[id] = parent;
            }
            p.WaitForExit();
            var all = new List<int>();
            var queue = new Queue<int>(new[] { pid });
            while (queue.Count > 0)
            {
                var x = queue.Dequeue();
                foreach (var kv in parents.Where(kv => kv.Value == x && kv.Key != x))
                {
                    all.Add(kv.Key);
                    queue.Enqueue(kv.Key);
                }
            }
            return all;
        }

        public static bool Alive(int pid)
        {
            try
            {
                using var p = Process.GetProcessById(pid);
                return !p.HasExited;
            }
            catch (Exception)
            {
                return false;
            }
        }

        public static string Name(int pid)
        {
            try
            {
                using var p = Process.GetProcessById(pid);
                return p.ProcessName;
            }
            catch (Exception)
            {
                return "";
            }
        }
    }
}
