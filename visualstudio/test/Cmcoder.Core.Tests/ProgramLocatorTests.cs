using Cmcoder.Core;
using System.Collections.Generic;
using System.IO;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>H1: which program starts, and that a project can never choose it.</summary>
    public class ProgramLocatorTests
    {
        private static readonly string Exe = ProgramLocator.Windows ? ".exe" : "";

        private static string Tmp() => Directory.CreateDirectory(Path.Combine(Path.GetTempPath(), "cmcoder-loc-" + System.Guid.NewGuid().ToString("N"))).FullName;

        private static string MakeProgram(string dir, string name)
        {
            Directory.CreateDirectory(dir);
            var p = Path.Combine(dir, name);
            File.WriteAllText(p, "#!/bin/sh\necho hi\n");
            Fixtures.MakeExecutable(p);
            return p;
        }

        [Fact]
        public void AProgramPlantedInTheProjectIsNeverStarted()
        {
            var tmp = Tmp();
            var project = Path.Combine(tmp, "project");
            MakeProgram(project, "cmcoder" + Exe);
            var tools = Path.Combine(tmp, "tools");
            var real = MakeProgram(tools, "cmcoder" + Exe);
            // The project folder first on PATH, a relative entry too: both skipped.
            var path = project + Path.PathSeparator + "." + Path.PathSeparator + tools;
            Assert.Equal(real, ProgramLocator.OnPath("cmcoder", project, new Dictionary<string, string?> { ["PATH"] = path, ["PATHEXT"] = ".EXE" }));
            Assert.Null(ProgramLocator.OnPath("cmcoder", project, new Dictionary<string, string?> { ["PATH"] = project }));
            Assert.Null(ProgramLocator.OnPath("cmcoder", project, new Dictionary<string, string?> { ["PATH"] = Path.Combine("relative", "dir") }));
        }

        [Fact]
        public void TheSettingThenTheBundledCopyThenPath()
        {
            var tmp = Tmp();
            var project = Directory.CreateDirectory(Path.Combine(tmp, "project")).FullName;
            var extension = Path.Combine(tmp, "extension");
            var bundled = MakeProgram(Path.Combine(extension, "bin", "cmcoder"), "cmcoder" + Exe);
            var onPath = MakeProgram(Path.Combine(tmp, "tools"), "cmcoder" + Exe);
            var mine = MakeProgram(Path.Combine(tmp, "mine"), "cmcoder" + Exe);
            var env = new Dictionary<string, string?> { ["PATH"] = Path.GetDirectoryName(onPath) };

            Assert.Equal(mine, ProgramLocator.Find(mine, extension, project, env).Program);
            Assert.Equal(bundled, ProgramLocator.Find(null, extension, project, env).Program);
            Assert.Equal(bundled, ProgramLocator.Find("  ", extension, project, env).Program);
            Assert.Equal(onPath, ProgramLocator.Find(null, Path.Combine(tmp, "none"), project, env).Program);
            var none = ProgramLocator.Find(null, null, project, new Dictionary<string, string?> { ["PATH"] = "" });
            Assert.Null(none.Program);
            Assert.Contains("Install the extension file again", none.Problem);
        }

        [Fact]
        public void TheSettingMustBeAFullPathToAProgram()
        {
            var tmp = Tmp();
            var project = Directory.CreateDirectory(Path.Combine(tmp, "project")).FullName;
            var empty = new Dictionary<string, string?>();
            Assert.Contains("full path", ProgramLocator.Find(Path.Combine("tools", "cmcoder"), null, project, empty).Problem);
            if (ProgramLocator.Windows) Assert.Contains("full path", ProgramLocator.Find(@"\tools\cmcoder.exe", null, project, empty).Problem);
            Assert.Contains("doesn't exist", ProgramLocator.Find(Path.Combine(tmp, "nope"), null, project, empty).Problem);
            var batch = MakeProgram(tmp, "cmcoder.cmd");
            Assert.Contains("batch file", ProgramLocator.Find(batch, null, project, empty).Problem);
        }

        [Fact]
        public void BatchFilesOnPathAreSkipped()
        {
            var tmp = Tmp();
            var tools = Path.Combine(tmp, "tools");
            MakeProgram(tools, "cmcoder.cmd");
            MakeProgram(tools, "cmcoder.bat");
            Assert.True(ProgramLocator.Batch("x.CMD") && ProgramLocator.Batch("x.bat"));
            if (ProgramLocator.Windows)
                Assert.Null(ProgramLocator.OnPath("cmcoder", tmp, new Dictionary<string, string?> { ["PATH"] = tools, ["PATHEXT"] = ".BAT;.CMD;.EXE" }));
        }
    }
}
