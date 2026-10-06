using Cmcoder.Core;
using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>
    /// What the tests run against: cmcoder (CMCODER_TEST_BINARY, the standalone
    /// program; else a wrapper around CMCODER_TEST_PYTHON on macOS and Linux) and
    /// the mock model server (from CMCODER_TEST_PYTHON: test equipment, not part
    /// of what developers get).
    /// </summary>
    internal static class Fixtures
    {
        public const string ApiKey = "sk-test-key";
        private static string? program;

        /// <summary>Skips the test when CMCODER_TEST_PYTHON isn't set (a plain build); CI always sets it.</summary>
        public static string Python()
        {
            var p = Environment.GetEnvironmentVariable("CMCODER_TEST_PYTHON");
            Skip.If(string.IsNullOrEmpty(p), "set CMCODER_TEST_PYTHON (a Python with cmcoder)");
            return p!;
        }

        public static string Program()
        {
            lock (typeof(Fixtures))
            {
                if (program != null) return program;
                var binary = Environment.GetEnvironmentVariable("CMCODER_TEST_BINARY");
                if (!string.IsNullOrEmpty(binary)) return program = Path.GetFullPath(binary!);
                Skip.If(ProgramLocator.Windows, "on Windows set CMCODER_TEST_BINARY (the standalone cmcoder.exe)");
                var dir = Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), "cmcoder-wrapper-" + Guid.NewGuid().ToString("N"))).FullName;
                var wrapper = Path.Combine(dir, "cmcoder");
                File.WriteAllText(wrapper, "#!/bin/sh\nexec '" + Python().Replace("'", "'\\''") + "' -m cmcoder \"$@\"\n");
                Fixtures.MakeExecutable(wrapper);
                return program = wrapper;
            }
        }

        /// <summary>The executable bit (macOS, Linux); nothing to do on Windows.</summary>
        public static void MakeExecutable(string file)
        {
            if (!OperatingSystem.IsWindows())
                File.SetUnixFileMode(file, UnixFileMode.UserRead | UnixFileMode.UserWrite | UnixFileMode.UserExecute);
        }

        public static string Project()
        {
            var dir = Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), "cmcoder-proj-" + Guid.NewGuid().ToString("N"))).FullName;
            Directory.CreateDirectory(Path.Combine(dir, ".git"));
            return dir;
        }

        /// <summary>The environment for cmcoder: the mock server, a private settings folder, no proxy.</summary>
        public static Dictionary<string, string?> Env(string baseUrl)
        {
            var env = new Dictionary<string, string?>
            {
                ["CMCODER_BASE_URL"] = baseUrl,
                ["CMCODER_API_KEY"] = ApiKey,
                ["CMCODER_MODEL"] = "qwen3-27b",
                ["CMCODER_CONFIG_DIR"] = Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), "cmcoder-config-" + Guid.NewGuid().ToString("N"))).FullName,
                ["PYTHON_KEYRING_BACKEND"] = "keyring.backends.fail.Keyring",
            };
            foreach (var proxy in new[] { "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy" })
                env[proxy] = null;
            return env;
        }

        public static void Until(string what, Func<bool> check, Func<string>? more = null, int timeoutMs = 60_000)
        {
            var end = DateTime.UtcNow.AddMilliseconds(timeoutMs);
            while (!check())
            {
                if (DateTime.UtcNow > end) throw new Xunit.Sdk.XunitException("timed out waiting for " + what + (more == null ? "" : "; " + more()));
                Thread.Sleep(50);
            }
        }
    }

    /// <summary>The mock model server with a script of replies (JSON array).</summary>
    internal sealed class MockServer : IDisposable
    {
        private readonly Process process;
        private readonly string record;

        public MockServer(string scriptJson)
        {
            var dir = Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), "cmcoder-mock-" + Guid.NewGuid().ToString("N"))).FullName;
            var script = Path.Combine(dir, "script.json");
            File.WriteAllText(script, scriptJson);
            record = Path.Combine(dir, "requests.jsonl");
            var psi = new ProcessStartInfo(Fixtures.Python())
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                StandardOutputEncoding = new UTF8Encoding(false),
            };
            foreach (var a in new[] { "-m", "cmcoder.testing.mock_server", "--script", script, "--port", "0", "--api-key", Fixtures.ApiKey, "--record", record })
                psi.ArgumentList.Add(a);
            process = Process.Start(psi)!;
            var line = process.StandardOutput.ReadLine();
            if (line == null || !line.StartsWith("mock server on ", StringComparison.Ordinal))
                throw new IOException("mock server didn't start: " + line);
            Url = line.Substring("mock server on ".Length).Trim();
        }

        public string Url { get; }

        /// <summary>The chat requests the model got.</summary>
        public List<JObject> Requests()
        {
            var list = new List<JObject>();
            if (!File.Exists(record)) return list;
            foreach (var line in File.ReadAllLines(record)) list.Add(Json.ParseObject(line)!);
            return list;
        }

        public void Dispose()
        {
            try { process.Kill(true); } catch (Exception) { }
            process.WaitForExit(10_000);
            process.Dispose();
        }
    }

}
