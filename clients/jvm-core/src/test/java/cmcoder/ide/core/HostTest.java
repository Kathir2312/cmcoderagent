package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assumptions.assumeFalse;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.function.BooleanSupplier;
import java.util.function.Consumer;
import org.junit.jupiter.api.Test;

/**
 * The shared host logic (H5-H18) with a fake IDE, against the real cmcoder:
 * what the Eclipse and NetBeans plugins get without writing it themselves.
 */
class HostTest {
    /** An IDE that records what it was asked to do. */
    static final class FakeIde implements Ide {
        final List<String> panel = Collections.synchronizedList(new ArrayList<>());
        final List<String> navigator = Collections.synchronizedList(new ArrayList<>());
        final List<String> calls = Collections.synchronizedList(new ArrayList<>());
        final List<Protocol.FileChange> diffs = Collections.synchronizedList(new ArrayList<>());
        final List<String> log = Collections.synchronizedList(new ArrayList<>());
        Map<String, Object> context;

        @Override public void toPanel(String json) { panel.add(json); }
        @Override public void toNavigator(String json) { navigator.add(json); }
        @Override public void openNavigator() { calls.add("openNavigator"); }
        @Override public void focusChat() { calls.add("focusChat"); }
        @Override public Map<String, Object> editorContext() { return context; }
        @Override public boolean autoContext() { return true; }
        @Override public boolean diffReview() { return true; }
        @Override public void openDiff(String id, Protocol.FileChange change) { calls.add("openDiff " + id); diffs.add(change); }
        @Override public void closeDiff(String id) { calls.add("closeDiff " + id); }
        @Override public List<String> ideTools() { return List.of("getDiagnostics", "openFile"); }

        @Override
        public void runIdeTool(String name, Map<String, Object> input, ToolDone done) {
            calls.add("tool " + name);
            // Answered on another thread, as an IDE would from its UI thread.
            new Thread(() -> done.done("app.py:1:1 warning: x is never used", false)).start();
        }

        @Override
        public void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen) {
            calls.add("pickRewind " + points.size());
            chosen.accept(new RewindChoice(points.get(0).turn, true, true, false));
        }

        @Override public void attachFile(Consumer<String> chosen) { chosen.accept("src\\main.py"); }
        @Override public void openExternal(String url) { calls.add("open " + url); }
        @Override public void log(String line) { log.add(line); }

        /** The panel messages of one kind. */
        List<Map<String, Object>> sent(String kind) {
            List<Map<String, Object>> out = new ArrayList<>();
            synchronized (panel) {
                for (String json : panel) {
                    Map<String, Object> m = Json.parseObject(json);
                    if (kind.equals(Json.string(m, "kind"))) out.add(m);
                }
            }
            return out;
        }

        /** The protocol events relayed to the panel, of one type. */
        List<Map<String, Object>> events(String type) {
            List<Map<String, Object>> out = new ArrayList<>();
            for (Map<String, Object> m : sent("event")) {
                Map<String, Object> e = Json.object(m, "event");
                if (type.equals(Json.string(e, "type"))) out.add(e);
            }
            return out;
        }
    }

    static void until(String what, BooleanSupplier check, FakeIde ide) throws InterruptedException {
        long end = System.currentTimeMillis() + 60_000;
        while (!check.getAsBoolean()) {
            if (System.currentTimeMillis() > end) throw new AssertionError("timed out waiting for " + what + "; log: " + ide.log);
            Thread.sleep(50);
        }
    }

    private static Host.Config config(Path project, String url) throws Exception {
        Host.Config c = new Host.Config();
        c.programSetting = Fixtures.program().toString();
        c.projectDir = project;
        c.client = "netbeans";
        c.product = "Acme Coder";
        c.env = Fixtures.env(url);
        return c;
    }

    @Test
    void aWholeConversationThroughTheHost() throws Exception {
        String script = "["
                + "{\"tool_calls\":[{\"name\":\"getDiagnostics\",\"arguments\":{}}]},"
                + "{\"content\":\"Checked the problems.\"},"
                + "{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"new.py\",\"content\":\"x = 2\\n\"}}]},"
                + "{\"content\":\"Wrote new.py.\"}]";
        try (Fixtures.MockServer server = new Fixtures.MockServer(script)) {
            Path project = Fixtures.project();
            Files.writeString(project.resolve("app.py"), "x = 1\n");
            FakeIde ide = new FakeIde();
            ide.context = EditorContext.of(project.resolve("app.py").toString(),
                    EditorContext.selection(project.resolve("app.py").toString(), 1, 1, "x = 1"),
                    List.of(EditorContext.diagnostic(project.resolve("app.py").toString(), 1, "warning", "x is never used", "lint")));
            Host host = new Host(ide, config(project, server.url));

            // The page loads: cmcoder starts; the panel gets every event, then "ready".
            host.onPanelMessage("{\"kind\":\"ready\"}");
            until("ready", () -> "ready".equals(host.state()), ide);
            assertEquals("starting", Json.string(ide.sent("state").get(0), "state"));
            assertEquals(1, ide.events("system_init").size());
            assertEquals("app.py:1 · 1 problem", Json.string(last(ide.sent("context")), "label"));

            // 1. The model asks the IDE for problems; the context went with the message.
            host.onPanelMessage("{\"kind\":\"send\",\"text\":\"any problems?\",\"includeContext\":true}");
            until("the first turn", () -> ide.events("result").size() == 1, ide);
            assertEquals("Checked the problems.", Json.string(ide.events("result").get(0), "result"));
            assertTrue(ide.calls.contains("tool getDiagnostics"));
            List<Map<String, Object>> toolResults = ide.events("tool_result");
            assertTrue(Json.string(toolResults.get(0), "content").contains("x is never used"));
            String firstRequest = Json.write(server.requests().get(0));
            assertTrue(firstRequest.contains("app.py") && firstRequest.contains("x is never used"),
                    "the editor context reached the model");

            // 2. An edit is shown in the IDE's diff viewer and accepted there.
            host.onPanelMessage("{\"kind\":\"send\",\"text\":\"create new.py\",\"includeContext\":false}");
            until("the diff", () -> !ide.diffs.isEmpty(), ide);
            Protocol.FileChange change = ide.diffs.get(0);
            assertTrue(change.path.endsWith("new.py"));
            assertEquals("x = 2\n", change.after);
            String id = Json.string(ide.events("permission_request").get(0), "request_id");
            host.answer(id, true, false, null); // the diff viewer's Accept
            until("the second turn", () -> ide.events("result").size() == 2, ide);
            assertEquals("x = 2\n", Files.readString(project.resolve("new.py")));
            assertTrue(ide.calls.contains("closeDiff " + id));
            assertEquals("Allowed", Json.string(last(ide.sent("permissionAnswered")), "text"));
            // The second message went without the editor context (the panel's toggle was off).
            List<Object> messages = Json.list(server.requests().get(2), "messages");
            String lastUser = String.valueOf(cast(messages.get(messages.size() - 1)).get("content"));
            assertTrue(lastUser.contains("create new.py"), lastUser);
            assertFalse(lastUser.contains("x is never used"), lastUser);

            // 3. The navigator, opened now, replays the turn.
            ide.navigator.clear();
            host.onNavigatorMessage("{\"kind\":\"ready\"}");
            Map<String, Object> reset = Json.parseObject(ide.navigator.get(0));
            assertEquals("reset", Json.string(reset, "kind"));
            assertEquals("create new.py", Json.string(reset, "prompt"));
            assertEquals("qwen3-27b", Json.string(reset, "model"));
            assertTrue(ide.navigator.stream().anyMatch(m -> m.contains("\"tool_use\"")), ide.navigator.toString());

            // 4. What the page can't make the IDE do.
            int resets = ide.sent("reset").size();
            host.onPanelMessage("{\"kind\":\"resume\",\"id\":\"--permission-mode=bypassPermissions\"}");
            host.onPanelMessage("{\"kind\":\"openLink\",\"href\":\"javascript:alert(1)\"}");
            host.onPanelMessage("{\"kind\":\"openLink\",\"href\":\"file:///etc/passwd\"}");
            host.onPanelMessage("{\"kind\":\"openLink\",\"href\":\"https://example.com/docs\"}");
            host.onPanelMessage("not json at all");
            host.onPanelMessage("{\"kind\":\"setMode\",\"mode\":\"root\"}");
            assertEquals(resets, ide.sent("reset").size(), "a bad session id doesn't restart anything");
            assertEquals(List.of("open https://example.com/docs"),
                    ide.calls.stream().filter(c -> c.startsWith("open ")).collect(java.util.stream.Collectors.toList()));
            assertTrue(host.running());

            // 5. The @ button: a project path, with forward slashes.
            host.onPanelMessage("{\"kind\":\"attachFile\"}");
            assertEquals("@src/main.py ", Json.string(last(ide.sent("prefill")), "text"));

            // 6. Closing the project stops cmcoder.
            host.dispose();
            assertFalse(host.running());
        }
    }

    @Test
    void anImpossibleProgramSettingIsExplained() throws Exception {
        FakeIde ide = new FakeIde();
        Host.Config c = new Host.Config();
        c.programSetting = "bin/cmcoder";
        c.projectDir = Fixtures.project();
        Host host = new Host(ide, c);
        host.onPanelMessage("{\"kind\":\"ready\"}");
        Map<String, Object> state = last(ide.sent("state"));
        assertEquals("exited", Json.string(state, "state"));
        assertTrue(Json.string(state, "message").contains("full path"));
        assertFalse(host.running());
    }

    @Test
    void noProjectNoStart() {
        FakeIde ide = new FakeIde();
        Host host = new Host(ide, new Host.Config());
        host.onPanelMessage("{\"kind\":\"ready\"}");
        assertTrue(Json.string(last(ide.sent("state")), "message").contains("Open a project first"));
    }

    @Test
    void aFirstLineSentAtOnceIsNotMissed() throws Exception {
        // A program that answers before start() has even returned (seen on CI:
        // the first event was dropped as coming from "no process").
        assumeFalse(ProgramLocator.windows(), "uses a shell script as the program");
        Path project = Fixtures.project();
        Path fast = project.resolve("fast-cmcoder");
        Files.writeString(fast, "#!/bin/sh\necho '{\"type\":\"system_init\",\"protocol_version\":1,\"session_id\":\"s\",\"cwd\":\".\","
                + "\"model\":\"m\",\"provider\":\"p\",\"tools\":[],\"permission_mode\":\"default\"}'\ncat > /dev/null\n", StandardCharsets.UTF_8);
        assertTrue(fast.toFile().setExecutable(true));
        for (int i = 0; i < 25; i++) {
            FakeIde ide = new FakeIde();
            Host.Config c = new Host.Config();
            c.programSetting = fast.toString();
            c.projectDir = project;
            Host host = new Host(ide, c);
            host.onPanelMessage("{\"kind\":\"ready\"}");
            long end = System.currentTimeMillis() + 10_000;
            while (!"ready".equals(host.state()) && System.currentTimeMillis() < end) Thread.sleep(10);
            assertEquals("ready", host.state(), "run " + i + ": the first line was lost");
            host.dispose();
        }
    }

    @Test
    void aProgramSpeakingAnotherProtocolIsStoppedWithAReason() throws Exception {
        assumeFalse(ProgramLocator.windows(), "uses a shell script as the program");
        Path project = Fixtures.project();
        Path fake = project.resolve("old-cmcoder");
        Files.writeString(fake, "#!/bin/sh\necho '{\"type\":\"system_init\",\"protocol_version\":2,\"session_id\":\"s\",\"cwd\":\".\","
                + "\"model\":\"m\",\"provider\":\"p\",\"tools\":[],\"permission_mode\":\"default\"}'\ncat > /dev/null\n", StandardCharsets.UTF_8);
        assertTrue(fake.toFile().setExecutable(true));
        FakeIde ide = new FakeIde();
        Host.Config c = new Host.Config();
        c.programSetting = fake.toString();
        c.projectDir = project;
        Host host = new Host(ide, c);
        host.onPanelMessage("{\"kind\":\"ready\"}");
        until("the mismatch", () -> ide.sent("state").stream().anyMatch(s -> String.valueOf(s.get("message")).contains("don't match")), ide);
        until("it stopped", () -> !host.running(), ide);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> cast(Object o) {
        return (Map<String, Object>) o;
    }

    private static Map<String, Object> last(List<Map<String, Object>> messages) {
        assertFalse(messages.isEmpty());
        return messages.get(messages.size() - 1);
    }
}
