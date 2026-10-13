using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

namespace Cmcoder.Core
{
    /// <summary>
    /// One <c>cmcoder --protocol stdio</c> process: one conversation (H2-H4), as
    /// the VS Code extension's agentProcess.ts and the JVM core's AgentProcess:
    /// started without a shell and without a console window; stdout read as
    /// UTF-8 lines, each an event (anything else and stderr to the log);
    /// stopped with <c>shutdown</c>, then end of input, then by force, with
    /// everything it started (on Windows its processes are in a job object that
    /// also ends them if Visual Studio itself goes away).
    /// Listener calls come from reader threads.
    /// </summary>
    public sealed class AgentProcess : IDisposable
    {
        public interface IListener
        {
            void OnEvent(Protocol.Event e);

            /// <summary>stderr, and stdout lines that aren't events.</summary>
            void OnLog(string line);

            /// <summary>It ended. expected: it was asked to stop. error: why it couldn't start (null otherwise).</summary>
            void OnExit(int? code, bool expected, string? error);
        }

        private static readonly Regex Ansi = new Regex("\u001B\\[[0-?]*[ -/]*[@-~]", RegexOptions.Compiled);
        private static readonly UTF8Encoding Utf8 = new UTF8Encoding(false);

        private readonly Process? process;
        private readonly IListener listener;
        private readonly object writeLock = new object();
        private readonly ManualResetEventSlim done = new ManualResetEventSlim(false);
        private readonly IntPtr job;
        private int exited;
        private volatile bool stopping;

        private AgentProcess(Process? process, IListener listener, IntPtr job)
        {
            this.process = process;
            this.listener = listener;
            this.job = job;
        }

        /// <summary>
        /// Starts cmcoder. A start that fails is reported through OnExit (never
        /// thrown), like a crash. env: variables to add or change (null removes one).
        /// </summary>
        public static AgentProcess Start(string program, IList<string> extraArgs, string cwd, IDictionary<string, string?> env, IListener listener)
        {
            var args = new List<string> { "--protocol", "stdio" };
            args.AddRange(extraArgs);
            var psi = new ProcessStartInfo(program, CommandLine(args))
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                WorkingDirectory = cwd,
                RedirectStandardInput = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Utf8,
                StandardErrorEncoding = Utf8,
            };
            foreach (var e in env)
            {
                if (e.Value == null) psi.Environment.Remove(e.Key);
                else psi.Environment[e.Key] = e.Value;
            }
            psi.Environment["NO_COLOR"] = "1";
            Process p;
            try
            {
                p = Process.Start(psi)!;
            }
            catch (Exception e) when (e is Win32Exception || e is IOException || e is InvalidOperationException)
            {
                var failed = new AgentProcess(null, listener, IntPtr.Zero);
                failed.Finish(null, DescribeStartError(program, e));
                return failed;
            }
            var agent = new AgentProcess(p, listener, Job.ForProcess(p));
            var outReader = agent.Read(p.StandardOutput, true);
            var errReader = agent.Read(p.StandardError, false);
            var waiter = new Thread(() =>
            {
                p.WaitForExit();
                // Every line it wrote is handled before its end is reported.
                outReader.Join(5000);
                errReader.Join(2000);
                agent.Finish(p.ExitCode, null);
            }) { IsBackground = true, Name = "cmcoder-exit" };
            waiter.Start();
            return agent;
        }

        /// <summary>The process id (tests, the log).</summary>
        public int? Id => process?.Id;

        public bool Running => Volatile.Read(ref exited) == 0;

        /// <summary>Sends one message (a JSON object); false if cmcoder isn't running.</summary>
        public bool Send(string json)
        {
            if (!Running || process == null) return false;
            var line = Utf8.GetBytes(json + "\n");
            lock (writeLock)
            {
                try
                {
                    var stdin = process.StandardInput.BaseStream;
                    stdin.Write(line, 0, line.Length);
                    stdin.Flush();
                    return true;
                }
                catch (Exception e) when (e is IOException || e is ObjectDisposedException || e is InvalidOperationException)
                {
                    return false; // it went away; OnExit reports it
                }
            }
        }

        /// <summary>Asks cmcoder to finish (it saves the session), then makes sure it and everything it started are gone.</summary>
        public void Stop(int timeoutMs = 5000)
        {
            if (!Running || process == null) return;
            stopping = true;
            Send(Protocol.Shutdown());
            lock (writeLock)
            {
                try { process.StandardInput.Close(); } catch (Exception) { /* already closed */ }
            }
            // Its processes, taken now: once it has exited they can't be found through it.
            var started = Descendants(process.Id);
            if (!done.Wait(timeoutMs)) KillTree();
            if (!done.Wait(5000)) KillTree();
            // Whatever it started and left behind (a shell command, a console host).
            if (!Job.Terminate(job)) EndLeftovers(started, 2000);
        }

        /// <summary>
        /// The processes under pid. Only used where there's no job object (macOS,
        /// Linux: the tests); on Windows the job holds them all.
        /// </summary>
        internal static List<int> Descendants(int pid)
        {
            var all = new List<int>();
            if (ProgramLocator.Windows) return all;
            var parents = Directory.Exists("/proc") ? ParentsFromProc() : ParentsFromPs();
            var queue = new Queue<int>();
            queue.Enqueue(pid);
            while (queue.Count > 0)
            {
                var p = queue.Dequeue();
                foreach (var kv in parents)
                {
                    if (kv.Value != p || kv.Key == p) continue;
                    all.Add(kv.Key);
                    queue.Enqueue(kv.Key);
                }
            }
            return all;
        }

        /// <summary>Linux: each process's parent, from /proc.</summary>
        private static Dictionary<int, int> ParentsFromProc()
        {
            var parents = new Dictionary<int, int>();
            foreach (var dir in Directory.GetDirectories("/proc"))
            {
                if (!int.TryParse(Path.GetFileName(dir), out var id)) continue;
                try
                {
                    // "pid (comm) state ppid ...": comm may hold spaces, so read after the last ')'.
                    var stat = File.ReadAllText(Path.Combine(dir, "stat"));
                    var fields = stat.Substring(stat.LastIndexOf(')') + 2).Split(' ');
                    parents[id] = int.Parse(fields[1], System.Globalization.CultureInfo.InvariantCulture);
                }
                catch (Exception) { /* it ended meanwhile */ }
            }
            return parents;
        }

        /// <summary>macOS (no /proc): each process's parent, from ps.</summary>
        private static Dictionary<int, int> ParentsFromPs()
        {
            var parents = new Dictionary<int, int>();
            try
            {
                var psi = new ProcessStartInfo("/bin/ps", "-A -o pid= -o ppid=") { UseShellExecute = false, RedirectStandardOutput = true };
                using var ps = Process.Start(psi)!;
                string? line;
                while ((line = ps.StandardOutput.ReadLine()) != null)
                {
                    var parts = line.Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                    if (parts.Length == 2 && int.TryParse(parts[0], out var id) && int.TryParse(parts[1], out var parent)) parents[id] = parent;
                }
                ps.WaitForExit(10_000);
            }
            catch (Exception) { /* no ps: nothing to clean up after */ }
            return parents;
        }

        /// <summary>Ends what's still running of these after a short grace (they normally end with cmcoder).</summary>
        internal static void EndLeftovers(List<int> pids, int graceMs)
        {
            var end = DateTime.UtcNow.AddMilliseconds(graceMs);
            while (DateTime.UtcNow < end && pids.Exists(Alive)) Thread.Sleep(100);
            foreach (var pid in pids)
            {
                try
                {
                    using var p = Process.GetProcessById(pid);
                    p.Kill();
                }
                catch (Exception) { /* gone */ }
            }
        }

        private static bool Alive(int pid)
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

        private void KillTree()
        {
            if (Job.Terminate(job)) return;
            try
            {
#if NET
                process!.Kill(true);
#else
                process!.Kill();
#endif
            }
            catch (Exception) { /* already gone */ }
        }

        /// <summary>Waits for the end (tests).</summary>
        public bool WaitForExit(int timeoutMs) => done.Wait(timeoutMs);

        public void Dispose()
        {
            Stop();
            Job.Close(job);
            process?.Dispose();
        }

        private Thread Read(StreamReader reader, bool stdout)
        {
            var t = new Thread(() =>
            {
                try
                {
                    string? line;
                    while ((line = reader.ReadLine()) != null)
                    {
                        if (!stdout)
                        {
                            SafeLog(Ansi.Replace(line, ""));
                            continue;
                        }
                        if (line.Trim().Length == 0) continue;
                        var e = Protocol.Event.Parse(line);
                        try
                        {
                            if (e == null) listener.OnLog("[stdout] " + line);
                            else listener.OnEvent(e);
                        }
                        catch (Exception ex)
                        {
                            // Never out of this thread: inside Visual Studio an unhandled
                            // exception on a thread ends Visual Studio itself.
                            SafeLog("Handling " + (e?.Type ?? "a line") + " failed: " + ex);
                        }
                    }
                }
                catch (Exception)
                {
                    // the stream closed with the process (or reading failed: the exit still comes)
                }
            }) { IsBackground = true, Name = stdout ? "cmcoder-stdout" : "cmcoder-stderr" };
            t.Start();
            return t;
        }

        private void SafeLog(string line)
        {
            try { listener.OnLog(line); }
            catch (Exception) { /* nowhere left to report it */ }
        }

        private void Finish(int? code, string? error)
        {
            if (Interlocked.Exchange(ref exited, 1) != 0) return;
            try { listener.OnExit(code, stopping && error == null, error); }
            catch (Exception ex) { SafeLog("Handling the end of cmcoder failed: " + ex); }
            finally { done.Set(); }
        }

        internal static string DescribeStartError(string program, Exception e)
        {
            var native = (e as Win32Exception)?.NativeErrorCode;
            if (native == 2 || native == 3 || e is FileNotFoundException)
                return "Couldn't start \"" + program + "\": not found. Install the extension again (it includes cmcoder), "
                    + "or set the cmcoder program's full path in Tools > Options > cmcoder.";
            if (native == 5 || native == 13)
                return "Couldn't start \"" + program + "\": it isn't allowed to run (access denied). "
                    + "Install the extension again, or check that your company's security software allows it.";
            return "Couldn't start \"" + program + "\": " + e.Message;
        }

        /// <summary>
        /// The command line for these arguments, quoted the way Windows programs
        /// (and .NET) split it again (CommandLineToArgvW), so an argument is never
        /// split or merged. .NET Framework has no ArgumentList.
        /// </summary>
        public static string CommandLine(IEnumerable<string> args)
        {
            var sb = new StringBuilder();
            foreach (var a in args)
            {
                if (sb.Length > 0) sb.Append(' ');
                Quote(a, sb);
            }
            return sb.ToString();
        }

        private static void Quote(string arg, StringBuilder sb)
        {
            if (arg.Length > 0 && arg.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
            {
                sb.Append(arg);
                return;
            }
            sb.Append('"');
            var backslashes = 0;
            foreach (var c in arg)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                if (c == '"')
                {
                    sb.Append('\\', backslashes * 2 + 1);
                    sb.Append('"');
                }
                else
                {
                    sb.Append('\\', backslashes);
                    sb.Append(c);
                }
                backslashes = 0;
            }
            sb.Append('\\', backslashes * 2);
            sb.Append('"');
        }

        /// <summary>A Windows job object that ends all its processes when terminated or closed.</summary>
        private static class Job
        {
            public static IntPtr ForProcess(Process p)
            {
                if (!ProgramLocator.Windows) return IntPtr.Zero;
                try
                {
                    var job = CreateJobObject(IntPtr.Zero, null);
                    if (job == IntPtr.Zero) return IntPtr.Zero;
                    var info = new JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                    info.BasicLimitInformation.LimitFlags = 0x2000; // JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                    var size = Marshal.SizeOf(typeof(JOBOBJECT_EXTENDED_LIMIT_INFORMATION));
                    var ptr = Marshal.AllocHGlobal(size);
                    try
                    {
                        Marshal.StructureToPtr(info, ptr, false);
                        if (!SetInformationJobObject(job, 9 /* ExtendedLimitInformation */, ptr, (uint)size)
                            || !AssignProcessToJobObject(job, p.Handle))
                        {
                            CloseHandle(job);
                            return IntPtr.Zero;
                        }
                    }
                    finally
                    {
                        Marshal.FreeHGlobal(ptr);
                    }
                    return job;
                }
                catch (Exception)
                {
                    return IntPtr.Zero;
                }
            }

            public static bool Terminate(IntPtr job) => job != IntPtr.Zero && TerminateJobObject(job, 1);

            public static void Close(IntPtr job)
            {
                if (job != IntPtr.Zero) CloseHandle(job);
            }

            [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
            private static extern IntPtr CreateJobObject(IntPtr attributes, string? name);

            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool SetInformationJobObject(IntPtr job, int infoClass, IntPtr info, uint length);

            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);

            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool TerminateJobObject(IntPtr job, uint exitCode);

            [DllImport("kernel32.dll", SetLastError = true)]
            private static extern bool CloseHandle(IntPtr handle);

            [StructLayout(LayoutKind.Sequential)]
            private struct JOBOBJECT_BASIC_LIMIT_INFORMATION
            {
                public long PerProcessUserTimeLimit;
                public long PerJobUserTimeLimit;
                public uint LimitFlags;
                public UIntPtr MinimumWorkingSetSize;
                public UIntPtr MaximumWorkingSetSize;
                public uint ActiveProcessLimit;
                public UIntPtr Affinity;
                public uint PriorityClass;
                public uint SchedulingClass;
            }

            [StructLayout(LayoutKind.Sequential)]
            private struct IO_COUNTERS
            {
                public ulong ReadOperationCount;
                public ulong WriteOperationCount;
                public ulong OtherOperationCount;
                public ulong ReadTransferCount;
                public ulong WriteTransferCount;
                public ulong OtherTransferCount;
            }

            [StructLayout(LayoutKind.Sequential)]
            private struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
            {
                public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
                public IO_COUNTERS IoInfo;
                public UIntPtr ProcessMemoryLimit;
                public UIntPtr JobMemoryLimit;
                public UIntPtr PeakProcessMemoryUsed;
                public UIntPtr PeakJobMemoryUsed;
            }
        }
    }
}
