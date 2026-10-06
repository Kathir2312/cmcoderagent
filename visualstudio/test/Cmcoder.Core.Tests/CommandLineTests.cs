using Cmcoder.Core;
using Newtonsoft.Json.Linq;
using System.Diagnostics;
using System.Linq;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>
    /// Arguments reach cmcoder exactly as given (.NET Framework, which Visual
    /// Studio runs, has no ArgumentList: the extension quotes them itself).
    /// </summary>
    public class CommandLineTests
    {
        private static readonly string[] Tricky =
        {
            "plain", "with space", "", "quote\"inside", "trailing\\", "trailing space\\", "back\\\\slash\\\"quote",
            "--resume=abc-123", "C:\\Program Files\\x\\", "tab\there", "ünïcødé ✓", "\"", "\\\\server\\share",
        };

        [SkippableFact]
        public void EveryArgumentArrivesUnchanged()
        {
            // A real program splits the line again (on Windows the C runtime's rules;
            // .NET applies the same rules on macOS and Linux).
            var psi = new ProcessStartInfo(Fixtures.Python(), "-c \"import sys,json; print(json.dumps(sys.argv[1:]))\" " + AgentProcess.CommandLine(Tricky))
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                StandardOutputEncoding = new System.Text.UTF8Encoding(false),
            };
            psi.Environment["PYTHONIOENCODING"] = "utf-8";
            using var p = Process.Start(psi)!;
            var output = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            Assert.Equal(Tricky, JArray.Parse(output).Select(t => (string)t!).ToArray());
        }

        [Fact]
        public void SimpleArgumentsStayReadable()
        {
            Assert.Equal("--protocol stdio --client visualstudio", AgentProcess.CommandLine(new[] { "--protocol", "stdio", "--client", "visualstudio" }));
            Assert.Equal("\"a b\" \"\" \"x\\\"y\"", AgentProcess.CommandLine(new[] { "a b", "", "x\"y" }));
        }
    }
}
