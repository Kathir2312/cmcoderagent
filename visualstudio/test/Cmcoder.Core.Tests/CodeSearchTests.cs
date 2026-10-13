using Cmcoder.Core;
using System.IO;
using System.Linq;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>Code search (H16) through the host, against the real cmcoder: set up, index, clear.</summary>
    public class CodeSearchTests
    {
        [SkippableFact]
        public void SetUpIndexAndClear()
        {
            using var server = new MockServer("[]");
            var project = Fixtures.Project();
            File.WriteAllText(Path.Combine(project, "auth.py"), "def refresh_auth_token(token):\n    return token\n");
            var ide = new HostTests.FakeIde();
            var host = new Host(ide, new Host.Config
            {
                ProgramSetting = Fixtures.Program(),
                ProjectDir = project,
                Client = "visualstudio",
                Env = Fixtures.Env(server.Url),
            });
            var changes = 0;
            var search = new CodeSearch(host, _ => System.Threading.Interlocked.Increment(ref changes));

            // Not running yet: a request says so at once.
            Assert.Null(search.CandidatesAsync().Result);

            host.OnPanelMessage("{\"kind\":\"ready\"}");
            Fixtures.Until("the index status", () => search.Text != null && changes > 0, ide.LogText);
            Assert.Equal("Code search: off", search.Text);
            Assert.False(search.IsSetUp);
            Assert.Contains("set up", search.Tooltip);

            var candidates = search.CandidatesAsync().Result!;
            Assert.Empty(candidates.Errors);
            var bad = search.SetUpAsync("default:qwen3-27b", "local", null, null, "user", false, false).Result!;
            Assert.False(bad.Ok);
            Assert.Contains("embedding model", bad.Message);
            var good = search.SetUpAsync("text-embedding-3-small", "local", null, null, "user", false, true).Result!;
            Assert.True(good.Ok, good.Message);
            Assert.Contains("Indexed 1 files", good.Message);
            Fixtures.Until("the new status", () => search.Text == "Code search: 1 files", ide.LogText);
            Assert.True(search.IsSetUp);
            Assert.Contains(search.Lines, l => l.Contains("Automatic context"));

            Assert.Equal(1, Json.Num(search.IndexAsync("update").Result!.Fields, "files", -1));
            search.IndexAsync("clear").Wait();
            Fixtures.Until("the cleared index", () => search.Text == "Code search: not indexed", ide.LogText);
            host.Dispose();
        }
    }
}
