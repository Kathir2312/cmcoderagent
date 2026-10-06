package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import org.junit.jupiter.api.Test;

/** Code search (H16) through the host, against the real cmcoder: set up, index, clear. */
class CodeSearchTest {
    @Test
    void setUpIndexAndClear() throws Exception {
        try (Fixtures.MockServer server = new Fixtures.MockServer("[]")) {
            Path project = Fixtures.project();
            Files.writeString(project.resolve("auth.py"), "def refresh_auth_token(token):\n    return token\n");
            HostTest.FakeIde ide = new HostTest.FakeIde();
            Host.Config c = new Host.Config();
            c.programSetting = Fixtures.program().toString();
            c.projectDir = project;
            c.client = "jetbrains";
            c.env = Fixtures.env(server.url);
            Host host = new Host(ide, c);
            AtomicInteger changes = new AtomicInteger();
            CodeSearch search = new CodeSearch(host, s -> changes.incrementAndGet());

            // Not running yet: a request says so at once.
            assertNull(search.candidates().get(5, TimeUnit.SECONDS));

            // cmcoder starts: the status item learns code search is off.
            host.onPanelMessage("{\"kind\":\"ready\"}");
            HostTest.until("the index status", () -> search.text() != null && changes.get() > 0, ide);
            assertEquals("Code search: off", search.text());
            assertFalse(search.setUp());
            assertTrue(search.tooltip().contains("set up"));

            // The set-up: the gateway's models, a model that can't embed, then one that can.
            CodeSearch.Candidates candidates = search.candidates().get(60, TimeUnit.SECONDS);
            assertTrue(candidates.errors.isEmpty(), candidates.errors.toString());
            CodeSearch.Result bad = search.setUp("default:qwen3-27b", "local", null, null, "user", false, false)
                    .get(60, TimeUnit.SECONDS);
            assertFalse(bad.ok);
            assertTrue(bad.message.contains("embedding model"), bad.message);
            CodeSearch.Result good = search.setUp("text-embedding-3-small", "local", null, null, "user", false, true)
                    .get(120, TimeUnit.SECONDS);
            assertTrue(good.ok, good.message);
            assertTrue(good.message.contains("Indexed 1 files"), good.message);
            HostTest.until("the new status", () -> "Code search: 1 files".equals(search.text()), ide);
            assertTrue(search.setUp());
            List<String> lines = search.lines();
            assertTrue(lines.stream().anyMatch(l -> l.contains("Automatic context")), lines.toString());

            // The menu's actions: update changes nothing; clear empties the index.
            assertEquals(1L, Json.number(search.index("update").get(60, TimeUnit.SECONDS).fields, "files", -1));
            search.index("clear").get(60, TimeUnit.SECONDS);
            HostTest.until("the cleared index", () -> "Code search: not indexed".equals(search.text()), ide);
            host.dispose();
        }
    }
}
