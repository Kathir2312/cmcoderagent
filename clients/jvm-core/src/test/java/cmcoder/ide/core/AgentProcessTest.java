package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.Test;

/** Host duties H2-H4 against the real cmcoder and the mock model server (as agentProcess.test.ts). */
class AgentProcessTest {
    /** Collects what an AgentProcess reports. */
    static final class Recorder implements AgentProcess.Listener {
        final LinkedBlockingQueue<Protocol.Event> events = new LinkedBlockingQueue<>();
        final List<String> log = new ArrayList<>();
        volatile Integer code;
        volatile Boolean expected;
        volatile String error;

        @Override
        public void onEvent(Protocol.Event event) {
            events.add(event);
        }

        @Override
        public synchronized void onLog(String line) {
            log.add(line);
        }

        @Override
        public void onExit(Integer code, boolean expected, String error) {
            this.code = code;
            this.error = error;
            this.expected = expected;
        }

        Protocol.Event next(String type) throws InterruptedException {
            long end = System.currentTimeMillis() + 60_000;
            while (System.currentTimeMillis() < end) {
                Protocol.Event e = events.poll(1, TimeUnit.SECONDS);
                if (e != null && e.type.equals(type)) return e;
            }
            throw new AssertionError("no " + type + " event; log: " + log);
        }
    }

    @Test
    void aTurnWithAPermissionPromptAllowedThenDenied() throws Exception {
        String script = "[{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"hello.py\",\"content\":\"print('hi')\\n\"}}]},"
                + "{\"content\":\"Created hello.py.\"},"
                + "{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"nope.py\",\"content\":\"x\"}}]},"
                + "{\"content\":\"OK, I won't.\"}]";
        try (Fixtures.MockServer server = new Fixtures.MockServer(script)) {
            Path cwd = Fixtures.project();
            Recorder r = new Recorder();
            AgentProcess agent = AgentProcess.start(Fixtures.program(), List.of(), cwd, Fixtures.env(server.url), r);
            Protocol.Event init = r.next("system_init");
            assertEquals(1L, init.fields.get("protocol_version"));

            assertTrue(agent.send(Protocol.userMessage("create hello.py", null)));
            Protocol.Event ask = r.next("permission_request");
            assertEquals("Write", ask.string("name"));
            Protocol.FileChange change = Protocol.FileChange.of(ask.object("change"));
            assertNotNull(change);
            assertNull(change.before); // a new file
            assertEquals("print('hi')\n", change.after);
            agent.send(Protocol.permissionResponse(ask.string("request_id"), true, false, null));
            assertEquals("Created hello.py.", r.next("result").string("result"));
            assertEquals("print('hi')\n", Files.readString(cwd.resolve("hello.py")));

            agent.send(Protocol.userMessage("create nope.py", null));
            Protocol.Event ask2 = r.next("permission_request");
            agent.send(Protocol.permissionResponse(ask2.string("request_id"), false, false, "don't"));
            assertEquals("OK, I won't.", r.next("result").string("result"));
            assertFalse(Files.exists(cwd.resolve("nope.py")));

            agent.stop();
            assertTrue(agent.awaitExit(10_000));
            assertEquals(0, r.code);
            assertEquals(Boolean.TRUE, r.expected);
            assertNull(r.error);
            assertFalse(agent.send(Protocol.interrupt())); // gone: sending reports it
        }
    }

    @Test
    void textOutsideAsciiArrivesIntact() throws Exception {
        String text = "Développement ä ✓ 😀 中文 — fin";
        try (Fixtures.MockServer server = new Fixtures.MockServer("[{\"content\":" + Json.write(text) + "}]")) {
            Recorder r = new Recorder();
            AgentProcess agent = AgentProcess.start(Fixtures.program(), List.of(), Fixtures.project(), Fixtures.env(server.url), r);
            r.next("system_init");
            agent.send(Protocol.userMessage("é?", null));
            assertEquals(text, r.next("result").string("result"));
            Map<String, Object> request = server.requests().get(0);
            assertTrue(Json.write(request).contains("é?"), "the message reached the model intact");
            agent.stop();
        }
    }

    @Test
    void aMissingProgramIsReportedNotThrown() throws Exception {
        Recorder r = new Recorder();
        Path missing = Fixtures.project().resolve("no-such-cmcoder" + (ProgramLocator.windows() ? ".exe" : ""));
        AgentProcess agent = AgentProcess.start(missing, List.of(), Fixtures.project(), Map.of(), r);
        assertTrue(agent.awaitExit(5_000));
        assertEquals(Boolean.FALSE, r.expected);
        assertTrue(r.error.contains("not found"), r.error);
        assertFalse(agent.running());
        assertFalse(agent.send(Protocol.interrupt()));
        agent.stop(); // harmless
    }

    @Test
    void aCrashIsReportedAsUnexpected() throws Exception {
        Recorder r = new Recorder();
        AgentProcess agent = AgentProcess.start(Fixtures.program(), List.of("--no-such-option"), Fixtures.project(), Map.of(), r);
        assertTrue(agent.awaitExit(60_000));
        assertEquals(Boolean.FALSE, r.expected);
        assertEquals(2, r.code);
        assertNull(r.error);
        assertTrue(String.join("\n", r.log).contains("no-such-option"), "the reason is in the log: " + r.log);
    }

    @Test
    void stoppingEndsEverythingItStarted() throws Exception {
        // A shell command still running when the project closes (host duty H3, gate G20).
        String script = "[{\"tool_calls\":[{\"name\":\"Bash\",\"arguments\":{\"command\":\"sleep 120\"}}]},{\"content\":\"done\"}]";
        try (Fixtures.MockServer server = new Fixtures.MockServer(script)) {
            Recorder r = new Recorder();
            AgentProcess agent = AgentProcess.start(Fixtures.program(), List.of("--permission-mode", "bypassPermissions"),
                    Fixtures.project(), Fixtures.env(server.url), r);
            r.next("system_init");
            agent.send(Protocol.userMessage("wait a bit", null));
            Protocol.Event use = r.next("tool_use");
            assertEquals("Bash", use.string("name"));
            List<ProcessHandle> children = new ArrayList<>();
            long end = System.currentTimeMillis() + 30_000;
            while (System.currentTimeMillis() < end) {
                children.clear();
                agent.handle().descendants().forEach(children::add);
                if (children.stream().anyMatch(h -> h.info().command().orElse("").contains("sleep")
                        || h.info().commandLine().orElse("").contains("sleep"))) break;
                Thread.sleep(200);
            }
            assertFalse(children.isEmpty(), "the shell command should be running under cmcoder");
            agent.stop(3_000);
            assertTrue(agent.awaitExit(15_000));
            long gone = System.currentTimeMillis() + 15_000;
            while (System.currentTimeMillis() < gone && children.stream().anyMatch(ProcessHandle::isAlive)) Thread.sleep(200);
            for (ProcessHandle h : children) assertFalse(h.isAlive(), "still running after stop: " + h.info());
        }
    }

    @Test
    void linesThatArentEventsGoToTheLog() throws Exception {
        org.junit.jupiter.api.Assumptions.assumeFalse(ProgramLocator.windows(), "uses a shell script as the program");
        Path fake = Fixtures.project().resolve("fake-cmcoder");
        Files.writeString(fake, "#!/bin/sh\necho 'not json'\necho '[1]'\necho '{\"type\":\"warning\",\"message\":\"w\"}'\necho oops >&2\nexit 0\n",
                StandardCharsets.UTF_8);
        assertTrue(fake.toFile().setExecutable(true));
        Recorder r = new Recorder();
        AgentProcess agent = AgentProcess.start(fake, List.of(), Fixtures.project(), Map.of(), r);
        assertTrue(agent.awaitExit(10_000));
        assertEquals("warning", r.next("warning").type);
        assertTrue(r.log.contains("[stdout] not json") && r.log.contains("[stdout] [1]") && r.log.contains("oops"), r.log.toString());
        assertEquals(Boolean.FALSE, r.expected); // it ended by itself
    }
}
