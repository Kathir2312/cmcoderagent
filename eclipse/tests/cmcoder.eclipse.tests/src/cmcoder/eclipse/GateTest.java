package cmcoder.eclipse;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertFalse;
import static org.junit.Assert.assertNotNull;
import static org.junit.Assert.assertTrue;
import static org.junit.Assume.assumeTrue;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.BooleanSupplier;

import org.eclipse.compare.CompareEditorInput;
import org.eclipse.core.resources.IFile;
import org.eclipse.core.resources.IMarker;
import org.eclipse.core.resources.IProject;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.swt.SWT;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Control;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Event;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IEditorReference;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.handlers.IHandlerService;
import org.eclipse.ui.ide.IDE;
import org.eclipse.ui.texteditor.ITextEditor;
import org.junit.AfterClass;
import org.junit.BeforeClass;
import org.junit.Test;

import cmcoder.ide.core.Json;
import cmcoder.ide.core.ProgramLocator;

/**
 * The release gate for Eclipse: a whole conversation in a real workbench,
 * through the real chat page (driven with the panel's test driver), a real
 * cmcoder and the mock model server. Nobody tests the plugin by hand before
 * developers get it, so everything a developer does first is here.
 */
public class GateTest {
    static final String API_KEY = "sk-test-gate-key";
    private static MockServer server;
    private static IProject project;

    @BeforeClass
    public static void setUp() throws Exception {
        String python = System.getenv("CMCODER_TEST_PYTHON");
        assumeTrue("set CMCODER_TEST_PYTHON (a Python with cmcoder) for the mock server", python != null && !python.isEmpty());
        String script = "["
                + "{\"tool_calls\":[{\"name\":\"getDiagnostics\",\"arguments\":{}}]},"
                + "{\"content\":\"Checked the problems.\"},"
                + "{\"tool_calls\":[{\"name\":\"Write\",\"arguments\":{\"file_path\":\"new.py\",\"content\":\"x = 2\\n\"}}]},"
                + "{\"content\":\"Wrote new.py.\"}]";
        server = new MockServer(python, script);

        project = ResourcesPlugin.getWorkspace().getRoot().getProject("demo");
        if (!project.exists()) project.create(null);
        project.open(null);
        Files.createDirectories(project.getLocation().toFile().toPath().resolve(".git"));
        IFile app = project.getFile("app.py");
        if (!app.exists()) app.create(new ByteArrayInputStream("x = 1\n".getBytes(StandardCharsets.UTF_8)), true, null);
        IMarker marker = app.createMarker(IMarker.PROBLEM);
        marker.setAttribute(IMarker.SEVERITY, IMarker.SEVERITY_WARNING);
        marker.setAttribute(IMarker.LINE_NUMBER, 1);
        marker.setAttribute(IMarker.MESSAGE, "x is never used");

        // The release build tests the plugin as developers get it: cmcoder from the
        // platform fragment (bin/cmcoder/), no program setting.
        Preferences.store().setValue(Preferences.PROGRAM, bundled() ? "" : program(python).toString());
        Map<String, String> env = new HashMap<>();
        env.put("CMCODER_BASE_URL", server.url);
        env.put("CMCODER_API_KEY", API_KEY);
        env.put("CMCODER_MODEL", "qwen3-27b");
        env.put("CMCODER_CONFIG_DIR", Files.createTempDirectory("cmcoder-config-").toString());
        env.put("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring");
        for (String proxy : new String[] {"HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"}) {
            env.put(proxy, null);
        }
        Session.get().testEnvironment(env);

        ui(() -> {
            PlatformUI.getWorkbench().getIntroManager().closeIntro(PlatformUI.getWorkbench().getIntroManager().getIntro());
            IEditorPart editor = IDE.openEditor(page(), app, "org.eclipse.ui.DefaultTextEditor");
            ((ITextEditor) editor).selectAndReveal(0, 5); // "x = 1"
            return null;
        });
    }

    @AfterClass
    public static void tearDown() {
        if (server != null) server.close();
    }

    @Test
    public void aWholeConversation() throws Exception {
        Session session = Session.get();
        ChatView view = ui(() -> (ChatView) page().showView(Session.CHAT_VIEW));
        Panel chat = view.panel();
        assertNotNull("the chat page has a browser", chat);

        // H1, H2, H5: the page loads and cmcoder (the program setting) starts.
        until("the page", () -> "complete".equals(String.valueOf(evaluate(chat, "return document.readyState"))));
        until("cmcoder ready", () -> "ready".equals(session.state()) && session.running());
        until("the page's test driver", () -> !"null".equals(String.valueOf(run(chat, "count", "textarea", null))));

        // H7: the open file, the selection and its problem are the context.
        until("the context label", () -> String.valueOf(run(chat, "text", ".context span", null)).contains("app.py:1 · 1 problem"));

        // H16: Eclipse's colours and fonts.
        String bg = ui(() -> Panel.hex(chat.browser().getParent().getBackground().getRGB()));
        assertEquals(bg, ui(() -> ((String) chat.browser().evaluate(
                "return getComputedStyle(document.documentElement).getPropertyValue('--vscode-sideBar-background').trim()"))));
        assertEquals(Brand.product(), evaluate(chat, "return document.body.dataset.product"));

        // A message with the context; the model asks Eclipse for its problems (H8).
        assertEquals("true", run(chat, "fill", "textarea", "any problems?"));
        assertEquals("true", run(chat, "press", "textarea", "Enter"));
        until("the first reply", () -> run(chat, "texts", ".msg.assistant", null).contains("Checked the problems."));
        List<Map<String, Object>> requests = server.requests();
        String first = Json.write(requests.get(0));
        assertTrue("the editor context reached the model: " + first, first.contains("app.py") && first.contains("x is never used"));
        String second = Json.write(requests.get(1));
        assertTrue("getDiagnostics answered from Eclipse's markers: " + second, second.contains("demo/app.py:1:1 warning: x is never used"));

        // H9, H10: the edit opens in the compare editor; Accept there writes it.
        assertEquals("true", run(chat, "fill", "textarea", "create new.py"));
        assertEquals("true", run(chat, "press", "textarea", "Enter"));
        until("the diff", () -> ui(() -> compareEditor() != null));
        ui(() -> {
            Button accept = find(compareEditor(), "cmcoder.eclipse.diff.accept");
            assertNotNull("the diff has Accept", accept);
            accept.notifyListeners(SWT.Selection, new Event());
            return null;
        });
        Path created = project.getLocation().toFile().toPath().resolve("new.py");
        until("new.py written", () -> Files.exists(created));
        until("the second reply", () -> run(chat, "texts", ".msg.assistant", null).contains("Wrote new.py."));
        assertEquals("x = 2\n", Files.readString(created));
        until("the diff closed", () -> ui(() -> compareEditor() == null));
        assertTrue(run(chat, "texts", ".permission .answer", null).contains("Allowed"));

        // H15: the navigator, opened now, shows the turn.
        NavigatorView nav = ui(() -> (NavigatorView) page().showView(Session.NAVIGATOR_VIEW));
        until("the navigator's turn", () -> String.valueOf(evaluate(nav.panel(),
                "return document.body.innerText")).contains("create new.py"));

        // H19: the page can't navigate away or open windows.
        String url = ui(() -> chat.browser().getUrl());
        ui(() -> chat.browser().execute("location.href='https://example.com/'"));
        Thread.sleep(1500);
        assertEquals(url, ui(() -> chat.browser().getUrl()));

        // H20: diagnostics to copy, with no key in them.
        Path projectDir = project.getLocation().toFile().toPath();
        String diagnostics = Commands.diagnostics(projectDir, true);
        assertTrue(diagnostics, diagnostics.contains("State: ready"));
        assertTrue(diagnostics, diagnostics.contains("cmcoder doctor --no-probe:"));
        assertFalse(diagnostics, diagnostics.contains("could not run it"));
        assertFalse(diagnostics.contains(API_KEY));
        if (bundled()) {
            assertTrue("cmcoder came from the plugin's fragment: " + diagnostics,
                    diagnostics.replace('\\', '/').matches("(?s).*Program: [^\n]*/cmcoder\\.eclipse\\.[a-z0-9_.]+/bin/cmcoder/cmcoder(\\.exe)?\n.*"));
        }

        // H14: "Ask About Selection" (the editor's menu and Ctrl+Alt+K) fills in a prompt.
        ui(() -> {
            page().activate(page().getActiveEditor());
            PlatformUI.getWorkbench().getService(IHandlerService.class).executeCommand("cmcoder.eclipse.askAboutSelection", null);
            return null;
        });
        until("the prompt", () -> String.valueOf(evaluate(chat, "return document.querySelector('textarea').value"))
                .startsWith("Explain the selected code."));

        // H3: closing the chat stops cmcoder.
        ui(() -> {
            page().hideView(view);
            return null;
        });
        until("cmcoder stopped", () -> !session.running());
    }

    // -- helpers -------------------------------------------------------------------------

    static IWorkbenchPage page() {
        return PlatformUI.getWorkbench().getWorkbenchWindows()[0].getActivePage();
    }

    static IEditorPart compareEditor() {
        for (IEditorReference ref : page().getEditorReferences()) {
            IEditorPart e = ref.getEditor(false);
            if (e != null && e.getEditorInput() instanceof CompareEditorInput) return e;
        }
        return null;
    }

    static Button find(IEditorPart editor, String key) {
        Control top = editor.getSite().getShell();
        return find(top, key);
    }

    private static Button find(Control c, String key) {
        if (c instanceof Button && key.equals(c.getData("org.eclipse.swtbot.widget.key")) && !c.isDisposed()) return (Button) c;
        if (c instanceof Composite) {
            for (Control child : ((Composite) c).getChildren()) {
                Button b = find(child, key);
                if (b != null) return b;
            }
        }
        return null;
    }

    static <T> T ui(Callable<T> call) {
        AtomicReference<T> result = new AtomicReference<>();
        AtomicReference<Exception> error = new AtomicReference<>();
        Display.getDefault().syncExec(() -> {
            try {
                result.set(call.call());
            } catch (Exception e) {
                error.set(e);
            }
        });
        if (error.get() != null) throw new RuntimeException(error.get());
        return result.get();
    }

    static Object evaluate(Panel panel, String script) {
        return ui(() -> panel.browser().evaluate(script));
    }

    /** The panel's test driver (dist/test-driver.js): JSON text. */
    static String run(Panel panel, String action, String selector, String value) {
        Object out = evaluate(panel, "return window.__cmcoderTest ? window.__cmcoderTest.run("
                + Json.write(action) + "," + Json.write(selector) + "," + (value == null ? "undefined" : Json.write(value)) + ") : 'null'");
        return String.valueOf(out);
    }

    static void until(String what, BooleanSupplier check) throws InterruptedException {
        long end = System.currentTimeMillis() + 90_000;
        while (!check.getAsBoolean()) {
            if (System.currentTimeMillis() > end) {
                throw new AssertionError("timed out waiting for " + what + "; state " + Session.get().state() + " " + Session.get().lastState()
                        + "; log:\n" + String.join("\n", Activator.get().recentLog()));
            }
            // Let the UI thread run (this is the test thread, not the UI thread).
            Thread.sleep(100);
        }
    }

    /** CMCODER_TEST_BUNDLED=1: the release build put cmcoder into this platform's fragment. */
    static boolean bundled() {
        return "1".equals(System.getenv("CMCODER_TEST_BUNDLED"));
    }

    /** cmcoder: CMCODER_TEST_BINARY (the standalone program), else a wrapper around the Python. */
    static Path program(String python) throws IOException {
        String binary = System.getenv("CMCODER_TEST_BINARY");
        if (binary != null && !binary.isEmpty()) return Path.of(binary).toAbsolutePath();
        assumeTrue("on Windows set CMCODER_TEST_BINARY (the standalone cmcoder.exe)", !ProgramLocator.windows());
        Path dir = Files.createTempDirectory("cmcoder-wrapper-");
        Path wrapper = dir.resolve("cmcoder");
        Files.writeString(wrapper, "#!/bin/sh\nexec '" + python.replace("'", "'\\''") + "' -m cmcoder \"$@\"\n");
        if (!wrapper.toFile().setExecutable(true)) throw new IOException("can't make " + wrapper + " executable");
        return wrapper;
    }

    /** The mock model server with a script of replies. */
    static final class MockServer implements AutoCloseable {
        final Process process;
        final String url;
        final Path record;

        MockServer(String python, String scriptJson) throws IOException {
            Path dir = Files.createTempDirectory("cmcoder-mock-");
            Path script = dir.resolve("script.json");
            Files.writeString(script, scriptJson);
            record = dir.resolve("requests.jsonl");
            List<String> command = new ArrayList<>(List.of(python, "-m", "cmcoder.testing.mock_server",
                    "--script", script.toString(), "--port", "0", "--api-key", API_KEY, "--record", record.toString()));
            process = new ProcessBuilder(command).redirectError(ProcessBuilder.Redirect.INHERIT).start();
            BufferedReader out = new BufferedReader(new InputStreamReader(process.getInputStream(), StandardCharsets.UTF_8));
            String line = out.readLine();
            if (line == null || !line.startsWith("mock server on ")) throw new IOException("mock server didn't start: " + line);
            url = line.substring("mock server on ".length()).trim();
        }

        List<Map<String, Object>> requests() throws IOException {
            List<Map<String, Object>> out = new ArrayList<>();
            if (!Files.exists(record)) return out;
            for (String line : Files.readAllLines(record, StandardCharsets.UTF_8)) out.add(Json.parseObject(line));
            return out;
        }

        @Override
        public void close() {
            process.destroy();
            try {
                process.waitFor(10, TimeUnit.SECONDS);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }
}
