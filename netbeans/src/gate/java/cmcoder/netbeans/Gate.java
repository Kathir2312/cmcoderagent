package cmcoder.netbeans;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

import javax.swing.SwingUtilities;
import javax.swing.text.JTextComponent;

import org.openide.LifecycleManager;
import org.openide.cookies.EditorCookie;
import org.openide.filesystems.FileObject;
import org.openide.filesystems.FileUtil;
import org.openide.loaders.DataObject;
import org.openide.windows.WindowManager;

import cmcoder.ide.core.Json;

/**
 * The release gate inside a real NetBeans (test builds only, never shipped):
 * with -J-Dcmcoder.gate=<result file> it runs a whole conversation through the
 * real module, its real chat page (the panel's test driver), the bundled
 * cmcoder and the mock model server (CMCODER_BASE_URL, set by the runner),
 * writes what happened, and closes NetBeans. netbeans/gate/run-gate.sh sets
 * it all up. The same steps as the Visual Studio and Eclipse gates.
 */
public final class Gate {
    private static final List<String> steps = new ArrayList<>();
    private static String result;
    private static volatile String seen = "";

    private Gate() {}

    /** Called by Installer when cmcoder.gate is set. */
    public static void start(String resultFile) {
        result = resultFile;
        System.setProperty(Panel.TEST_DRIVER, String.valueOf(Plugin.file("panel/test-driver.js")));
        WindowManager.getDefault().invokeWhenUIReady(() -> {
            Thread t = new Thread(Gate::runAndReport, "cmcoder gate");
            t.setDaemon(true);
            t.start();
        });
    }

    private static void runAndReport() {
        String failure = null;
        try {
            run();
        } catch (Throwable e) {
            java.io.StringWriter w = new java.io.StringWriter();
            e.printStackTrace(new java.io.PrintWriter(w));
            failure = w.toString();
            try {
                Panel chat = Session.get().chat();
                failure += "\nchat page: " + (chat == null ? "not open"
                        : chat.evaluate("document.URL + ' ' + document.readyState + ' host=' + typeof window.cmcoderHostPost"
                                + " + ' scripts=' + Array.from(document.scripts).map(function (s) { return s.src; }).join(',')"
                                + " + ' text=' + document.body.textContent.slice(0, 300)").get(10, TimeUnit.SECONDS));
            } catch (Exception probe) {
                failure += "\n(no page probe: " + probe + ")";
            }
        }
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("ok", failure == null);
        report.put("failure", failure);
        report.put("steps", steps);
        report.put("state", Session.get().state() + " " + Session.get().lastState());
        report.put("log", Plugin.recentLog());
        try {
            Files.writeString(Path.of(result), Json.write(report), StandardCharsets.UTF_8);
        } catch (IOException e) {
            Plugin.error("Could not write the gate's result", e);
        }
        LifecycleManager.getDefault().exit();
    }

    /** Also to a file as it happens: if NetBeans hangs, the runner shows how far it got. */
    private static void step(String what) {
        String line = LocalTime.now().format(DateTimeFormatter.ofPattern("HH:mm:ss")) + " " + what;
        steps.add(line);
        try {
            Files.writeString(Path.of(result + ".steps"), line + System.lineSeparator(), StandardCharsets.UTF_8,
                    StandardOpenOption.CREATE, StandardOpenOption.APPEND);
        } catch (IOException e) {
            // the report still has it
        }
    }

    private static void until(String what, Callable<Boolean> check, int seconds) throws Exception {
        long end = System.nanoTime() + TimeUnit.SECONDS.toNanos(seconds);
        while (!Boolean.TRUE.equals(check.call())) {
            if (System.nanoTime() > end) throw new AssertionError("timed out waiting for " + what + (seen.isEmpty() ? "" : " (" + seen + ")"));
            Thread.sleep(250);
        }
        step(what);
    }

    private static void until(String what, Callable<Boolean> check) throws Exception {
        until(what, check, 90);
    }

    private static <T> T ui(Callable<T> read) throws Exception {
        AtomicReference<T> value = new AtomicReference<>();
        AtomicReference<Exception> error = new AtomicReference<>();
        SwingUtilities.invokeAndWait(() -> {
            try {
                value.set(read.call());
            } catch (Exception e) {
                error.set(e);
            }
        });
        if (error.get() != null) throw error.get();
        return value.get();
    }

    /** The panel's test driver: run(action, selector, value) → its answer. */
    private static String driver(Panel panel, String action, String selector, String value) throws Exception {
        String script = "window.__cmcoderTest ? String(window.__cmcoderTest.run(" + Json.write(action) + "," + Json.write(selector) + ","
                + (value == null ? "undefined" : Json.write(value)) + ")) : 'null'";
        return panel.evaluate(script).get(30, TimeUnit.SECONDS);
    }

    private static String driver(Panel panel, String action, String selector) throws Exception {
        return driver(panel, action, selector, null);
    }

    private static void send(Panel panel, String text) throws Exception {
        if (!"true".equals(driver(panel, "fill", "textarea", text))) throw new AssertionError("couldn't type into the chat");
        if (!"true".equals(driver(panel, "press", "textarea", "Enter"))) throw new AssertionError("couldn't send");
    }

    private static void run() throws Exception {
        Session session = Session.get();
        step("loaded in NetBeans " + System.getProperty("netbeans.buildnumber", "?"));
        Path folder = Path.of(System.getProperty("cmcoder.gate.project"));
        File app = folder.resolve("app.txt").toFile();

        // H7: an open file with a selection.
        EditorCookie editor = ui(() -> {
            FileObject fo = FileUtil.toFileObject(FileUtil.normalizeFile(app));
            EditorCookie cookie = DataObject.find(fo).getLookup().lookup(EditorCookie.class);
            cookie.open();
            return cookie;
        });
        // Focus it as a click would (a virtual display has no window manager to do it).
        until("app.txt is the active editor", () -> ui(() -> {
            JTextComponent c = Workspace.editor();
            FileObject fo = c == null ? null : Workspace.file(c.getDocument());
            if (fo != null && fo.getNameExt().equals("app.txt")) return true;
            javax.swing.JEditorPane[] panes = editor.getOpenedPanes();
            if (panes != null && panes.length > 0) panes[0].requestFocus();
            org.openide.windows.Mode mode = WindowManager.getDefault().findMode("editor");
            seen = "panes=" + (panes == null ? "null" : panes.length) + " editorMode=" + (mode == null ? null : mode.getSelectedTopComponent())
                    + " opened=" + org.openide.windows.TopComponent.getRegistry().getOpened()
                    + " doc=" + (editor.getDocument() != null) + " mainWindow=" + WindowManager.getDefault().getMainWindow().isShowing();
            return false;
        }));
        ui(() -> {
            JTextComponent c = Workspace.editor();
            int end = c.getDocument().getDefaultRootElement().getElement(0).getEndOffset() - 1;
            c.select(0, end);
            return null;
        });
        Path project = ui(Workspace::projectDir);
        if (!folder.toRealPath().equals(project == null ? null : project.toRealPath())) {
            throw new AssertionError("the project folder is " + project + ", not " + folder);
        }

        // H1, H2, H5: the chat opens, cmcoder (the bundled one) starts.
        ChatWindow window = ui(ChatWindow::showIt);
        Panel chat = window.panel();
        if (chat == null) throw new AssertionError("the chat page couldn't start");
        until("cmcoder ready", () -> "ready".equals(session.state()) && session.running(), 180);
        until("the page's test driver", () -> "1".equals(driver(chat, "count", "textarea")));
        until("the context label", () -> driver(chat, "text", ".context span").contains("app.txt:1"));
        until("code search's status item", () -> "Code search: off".equals(ui(window::codeSearchText)));
        String diagnostics = Actions.diagnostics(folder, true);
        String bundled = File.separator + "bin" + File.separator + "cmcoder" + File.separator + "cmcoder";
        if (!diagnostics.contains(bundled)) throw new AssertionError("cmcoder didn't come from the plugin:\n" + diagnostics);
        if (!diagnostics.contains("cmcoder doctor --no-probe:")) throw new AssertionError("no doctor in the diagnostics");
        String key = System.getenv("CMCODER_API_KEY");
        if (key != null && !key.isEmpty() && diagnostics.contains(key)) throw new AssertionError("the API key is in the diagnostics");
        step("diagnostics: the bundled program, doctor, no key");

        // H8: a message; the model asks NetBeans for its problems.
        send(chat, "any problems?");
        until("the first reply", () -> driver(chat, "texts", ".msg.assistant").contains("Checked the problems."));

        // H9, H10: a change in the diff viewer, accepted there.
        DiffReview diffs = session.diffs();
        send(chat, "create new.txt");
        until("the diff tab", () -> ui(() -> diffs.openRequests().size() == 1));
        ui(() -> {
            diffs.click(diffs.openRequests().iterator().next(), DiffReview.ACCEPT);
            return null;
        });
        until("new.txt written", () -> Files.exists(folder.resolve("new.txt")));
        until("the diff closed", () -> ui(() -> diffs.openRequests().isEmpty()));
        until("the chat says Allowed", () -> driver(chat, "texts", ".permission .answer").contains("Allowed"));
        if (!"x = 2\n".equals(Files.readString(folder.resolve("new.txt")))) throw new AssertionError("new.txt has the wrong text");

        // Rejected there: nothing written.
        send(chat, "create other.txt");
        until("the second diff tab", () -> ui(() -> diffs.openRequests().size() == 1));
        ui(() -> {
            diffs.click(diffs.openRequests().iterator().next(), DiffReview.REJECT);
            return null;
        });
        until("the chat says Denied", () -> driver(chat, "texts", ".permission .answer").contains("Denied"));
        until("the second diff closed", () -> ui(() -> diffs.openRequests().isEmpty()));
        if (Files.exists(folder.resolve("other.txt"))) throw new AssertionError("other.txt was written after Reject");

        // H15: the navigator, opened now, shows the turn.
        NavigatorWindow navigator = ui(NavigatorWindow::showIt);
        Panel nav = navigator.panel();
        if (nav == null) throw new AssertionError("the navigator page couldn't start");
        until("the navigator's turn", () -> nav.evaluate("document.body.textContent").get(10, TimeUnit.SECONDS).contains("create other.txt"));

        // H19: the page can't navigate away.
        String url = chat.pageLocation().get(10, TimeUnit.SECONDS);
        chat.evaluate("location.href='https://example.com/'; 'ok'").get(10, TimeUnit.SECONDS);
        Thread.sleep(1500);
        String now = chat.pageLocation().get(10, TimeUnit.SECONDS);
        if (!url.equals(now)) throw new AssertionError("the chat page navigated away to " + now);
        if (!"1".equals(driver(chat, "count", "textarea"))) throw new AssertionError("the chat page is gone after a navigation attempt");
        step("the page stayed");

        // H14: Ask About Selection fills in a prompt.
        ui(() -> {
            new Actions.AskAboutSelection().actionPerformed(null);
            return null;
        });
        until("the prompt", () -> chat.evaluate("document.querySelector('textarea').value").get(10, TimeUnit.SECONDS)
                .contains("Explain the selected code."));

        // H3: closing the chat stops cmcoder.
        ui(() -> {
            ChatWindow.find().close();
            return null;
        });
        until("cmcoder stopped", () -> !session.running(), 30);
    }
}
