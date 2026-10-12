package cmcoder.idea;

import java.awt.Component;
import java.awt.Container;
import java.awt.Dialog;
import java.awt.Frame;
import java.awt.Rectangle;
import java.awt.Robot;
import java.awt.Window;
import java.awt.image.BufferedImage;
import java.io.File;
import java.io.IOException;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.time.LocalTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Callable;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import javax.imageio.ImageIO;
import javax.swing.AbstractButton;
import javax.swing.JFrame;
import javax.swing.JLabel;
import javax.swing.text.JTextComponent;

import com.intellij.openapi.actionSystem.ActionManager;
import com.intellij.openapi.actionSystem.ActionPlaces;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.actionSystem.CommonDataKeys;
import com.intellij.openapi.actionSystem.DataContext;
import com.intellij.openapi.actionSystem.impl.SimpleDataContext;
import com.intellij.openapi.application.ApplicationInfo;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.editor.Editor;
import com.intellij.openapi.fileEditor.FileEditor;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.fileEditor.TextEditor;
import com.intellij.openapi.project.DumbService;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.project.ProjectManager;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.intellij.openapi.vfs.VirtualFile;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowManager;
import com.intellij.openapi.wm.WindowManager;
import com.intellij.ui.content.Content;

import cmcoder.ide.core.Json;

/**
 * The release gate inside a real IntelliJ-based IDE (test builds only, never
 * shipped): with -Dcmcoder.gate=&lt;result file&gt; it runs a whole conversation
 * through the real plugin, its real chat page (the panel's test driver), the
 * bundled cmcoder and the mock model server (CMCODER_BASE_URL, set by the
 * runner), writes what happened, and closes the IDE. jetbrains/gate/run-gate.sh
 * sets it all up. The same steps as the NetBeans, Eclipse and Visual Studio
 * gates.
 */
public final class Gate {
    private static final List<String> steps = new ArrayList<>();
    private static String result;
    private static volatile String seen = "";
    private static volatile Project project;

    private Gate() {}

    /** Called by GateStarter when cmcoder.gate is set. */
    public static void start(String resultFile) {
        result = resultFile;
        Path driver = Plugin.file("panel/test-driver.js");
        System.setProperty(Panel.TEST_DRIVER, String.valueOf(driver));
        Thread t = new Thread(Gate::runAndReport, "cmcoder gate");
        t.setDaemon(true);
        t.start();
    }

    private static void runAndReport() {
        String failure = null;
        try {
            if (Plugin.file("panel/test-driver.js") == null) throw new AssertionError("no panel/test-driver.js: not the gate build?");
            run();
        } catch (Throwable e) {
            StringWriter w = new StringWriter();
            e.printStackTrace(new PrintWriter(w));
            failure = w.toString();
            failure += "\nchat page: " + probe();
            failure += "\ntool window: " + toolWindowState();
            failure += "\nwindows: " + windows();
        }
        Map<String, Object> report = new LinkedHashMap<>();
        report.put("ok", failure == null);
        report.put("failure", failure);
        report.put("steps", steps);
        Project p = project;
        if (p != null && !p.isDisposed()) {
            Session session = Session.get(p);
            report.put("state", session.state() + " " + session.lastState());
            report.put("log", session.recentLog());
        }
        try {
            Files.writeString(Path.of(result), Json.write(report), StandardCharsets.UTF_8);
        } catch (IOException e) {
            Plugin.error("Could not write the gate's result", e);
        }
        // Not "any" modality: closing saves settings, which needs a write-safe context.
        ApplicationManager.getApplication().invokeLater(() -> ApplicationManager.getApplication().exit(true, true, false));
    }

    /** The cmcoder tool window as the IDE sees it (read off the UI thread: for a failure's report only). */
    private static String toolWindowState() {
        try {
            Project p = project;
            if (p == null || p.isDisposed()) return "no project";
            ToolWindow w = ToolWindowManager.getInstance(p).getToolWindow(Session.TOOL_WINDOW);
            if (w == null) return "not registered (ids: " + List.of(ToolWindowManager.getInstance(p).getToolWindowIds()) + ")";
            return "available=" + w.isAvailable() + " visible=" + w.isVisible() + " active=" + w.isActive()
                    + " contents=" + w.getContentManager().getContentCount();
        } catch (RuntimeException e) {
            return "(couldn't read it: " + e + ")";
        }
    }

    /** What the chat page shows, for a failure's report. */
    private static String probe() {
        try {
            Project p = project;
            Panel chat = p == null || p.isDisposed() ? null : Session.get(p).chat();
            if (chat == null) return "not open";
            return chat.evaluate("document.URL + ' ' + document.readyState + ' host=' + typeof window.cmcoderHostPost"
                    + " + ' driver=' + typeof window.__cmcoderTest"
                    + " + ' scripts=' + Array.from(document.scripts).map(function (s) { return s.src; }).join(',')"
                    + " + ' text=' + document.body.textContent.slice(0, 300)").get(10, TimeUnit.SECONDS);
        } catch (Exception e) {
            return "(no page probe: " + e + ")";
        }
    }

    /**
     * A screenshot of the IDE's window, if -Dcmcoder.gate.shots=&lt;folder&gt; is
     * set (the release build keeps them as an artifact). Never fails the gate.
     */
    private static void shot(String name) {
        String folder = System.getProperty("cmcoder.gate.shots");
        if (folder == null || folder.isBlank()) return;
        try {
            Thread.sleep(1500); // the page and the window settle
            Rectangle bounds = ui(() -> {
                JFrame frame = WindowManager.getInstance().getFrame(project);
                if (frame == null) return null;
                frame.toFront();
                return frame.getBounds();
            });
            if (bounds == null) return;
            BufferedImage image = new Robot().createScreenCapture(bounds);
            Files.createDirectories(Path.of(folder));
            ImageIO.write(image, "png", Path.of(folder, "intellij-" + name + ".png").toFile());
        } catch (Exception e) {
            step("(no screenshot " + name + ": " + e + ")");
        }
    }

    /** Also to a file as it happens: if the IDE hangs, the runner shows how far it got. */
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
            if (System.nanoTime() > end) {
                throw new AssertionError("timed out waiting for " + what + (seen.isEmpty() ? "" : " (" + seen + ")"));
            }
            Thread.sleep(250);
        }
        seen = "";
        step(what);
    }

    private static void until(String what, Callable<Boolean> check) throws Exception {
        until(what, check, 90);
    }

    /** {@code read} on the UI thread, waiting for it (the diff windows are modeless: no modal dialog is in the way). */
    private static <T> T ui(Callable<T> read) throws Exception {
        CompletableFuture<T> result = new CompletableFuture<>();
        ApplicationManager.getApplication().invokeLater(() -> {
            try {
                result.complete(read.call());
            } catch (Exception e) {
                result.completeExceptionally(e);
            }
        });
        try {
            return result.get(60, TimeUnit.SECONDS);
        } catch (TimeoutException e) {
            // Waiting for a modal dialog to close, most likely: say which.
            throw new AssertionError("the IDE's UI thread didn't run a step within 60 s. Windows: " + windows());
        } catch (ExecutionException e) {
            if (e.getCause() instanceof Exception cause) throw cause;
            throw e;
        }
    }

    /** The windows on screen, with their titles and some of their text (for a failure's report). */
    private static String windows() {
        StringBuilder out = new StringBuilder();
        for (Window w : Window.getWindows()) {
            if (!w.isShowing()) continue;
            String title = w instanceof Dialog d ? d.getTitle() : w instanceof Frame f ? f.getTitle() : "";
            boolean modal = w instanceof Dialog d && d.isModal();
            StringBuilder text = new StringBuilder();
            texts(w, text);
            out.append("\n  ").append(w.getClass().getName()).append(modal ? " (modal)" : "")
                    .append(" \"").append(title).append("\": ")
                    .append(text.length() > 400 ? text.substring(0, 400) + "…" : text);
        }
        return out.length() == 0 ? "none" : out.toString();
    }

    private static void texts(Component c, StringBuilder out) {
        String t = c instanceof JLabel l ? l.getText() : c instanceof AbstractButton b ? "[" + b.getText() + "]"
                : c instanceof JTextComponent tc ? tc.getText() : null;
        if (t != null && !t.isBlank()) out.append(t.replaceAll("<[^>]+>", " ").replaceAll("\\s+", " ").trim()).append(" | ");
        if (c instanceof Container container) {
            for (Component child : container.getComponents()) texts(child, out);
        }
    }

    /** The panel's test driver: run(action, selector, value) → its answer (JSON text). */
    private static String driver(Panel panel, String action, String selector, String value) throws Exception {
        String script = "window.__cmcoderTest ? String(window.__cmcoderTest.run(" + Json.write(action) + "," + Json.write(selector) + ","
                + (value == null ? "undefined" : Json.write(value)) + ")) : 'null'";
        return panel.evaluate(script).get(40, TimeUnit.SECONDS);
    }

    private static String driver(Panel panel, String action, String selector) throws Exception {
        return driver(panel, action, selector, null);
    }

    private static void send(Panel panel, String text) throws Exception {
        if (!"true".equals(driver(panel, "fill", "textarea", text))) throw new AssertionError("couldn't type into the chat");
        if (!"true".equals(driver(panel, "press", "textarea", "Enter"))) throw new AssertionError("couldn't send");
    }

    private static void run() throws Exception {
        ApplicationInfo ide = ApplicationInfo.getInstance();
        step("loaded in " + ide.getFullApplicationName() + " (" + ide.getBuild().asString() + ")");
        Path folder = Path.of(System.getProperty("cmcoder.gate.project")).toRealPath();

        // The project the runner opened, indexed.
        until("the project is open", () -> {
            for (Project p : ProjectManager.getInstance().getOpenProjects()) {
                String base = p.getBasePath();
                if (p.isInitialized() && base != null && Path.of(base).toRealPath().equals(folder)) {
                    project = p;
                    return true;
                }
            }
            seen = "open: " + List.of(ProjectManager.getInstance().getOpenProjects());
            return false;
        }, 300);
        Project p = project;
        until("the project is indexed", () -> !DumbService.getInstance(p).isDumb(), 300);
        Session session = Session.get(p);

        // H21: the runner trusts the project (as a developer would): cmcoder may read its settings.
        if (!session.trusted()) throw new AssertionError("the project isn't trusted");
        step("the project is trusted");

        // H7: an open file with a selection (its first line).
        File app = folder.resolve("app.txt").toFile();
        ui(() -> {
            VirtualFile vf = LocalFileSystem.getInstance().refreshAndFindFileByIoFile(app);
            if (vf == null) throw new AssertionError("no app.txt in the project");
            Editor editor = FileEditorManager.getInstance(p).openTextEditor(new OpenFileDescriptor(p, vf, 0), true);
            if (editor == null) throw new AssertionError("app.txt didn't open in an editor");
            editor.getSelectionModel().setSelection(0, editor.getDocument().getLineEndOffset(0));
            return null;
        });
        until("app.txt is the selected editor", () -> ui(() -> {
            Editor editor = FileEditorManager.getInstance(p).getSelectedTextEditor();
            seen = "selected: " + (editor == null ? null : editor.getVirtualFile());
            return editor != null && editor.getSelectionModel().hasSelection()
                    && "app.txt".equals(editor.getVirtualFile() == null ? null : editor.getVirtualFile().getName());
        }));

        // H1, H2, H5: the chat opens, cmcoder (the bundled one) starts.
        session.showChat();
        until("the chat page", () -> session.chat() != null && session.chat().isLoaded(), 120);
        Panel chat = session.chat();
        until("cmcoder ready", () -> {
            seen = session.state() + " " + session.lastState();
            return "ready".equals(session.state()) && session.running();
        }, 180);
        until("the page's test driver", () -> "1".equals(driver(chat, "count", "textarea")));
        until("the context label", () -> {
            String label = driver(chat, "text", ".context span");
            seen = "label: " + label;
            return label.contains("app.txt:1");
        });
        // H16: code search's state, in the status bar.
        until("code search's status item", () -> {
            String text = session.codeSearch().text();
            seen = "status: " + text;
            return "Code search: off".equals(text);
        });

        // H22: Copy Diagnostics' text: the bundled program, its doctor, never the key.
        String diagnostics = Diagnostics.collect(p, true);
        // The plugin's copy, or the shared one in ~/.cmcoder/bin (the same build, copied there by an earlier start).
        String bundled = File.separator + "bin" + File.separator + "cmcoder" + File.separator + "cmcoder";
        String shared = File.separator + ".cmcoder" + File.separator + "bin" + File.separator + "cmcoder";
        if (!diagnostics.contains(bundled) && !diagnostics.contains(shared)) {
            throw new AssertionError("cmcoder didn't come from the plugin or ~/.cmcoder/bin:\n" + diagnostics);
        }
        if (!diagnostics.contains("cmcoder doctor --no-probe:")) throw new AssertionError("no doctor in the diagnostics:\n" + diagnostics);
        String key = System.getenv("CMCODER_API_KEY");
        if (key != null && !key.isEmpty() && diagnostics.contains(key)) throw new AssertionError("the API key is in the diagnostics");
        step("diagnostics: the bundled program, doctor, no key");

        // H8: a message; the model asks the IDE for its problems.
        send(chat, "any problems?");
        until("the first reply", () -> driver(chat, "texts", ".msg.assistant").contains("Checked the problems."));
        shot("1-chat");

        // H9, H10: a change in the diff viewer, accepted there.
        DiffReview diffs = session.diffs();
        send(chat, "create new.txt");
        until("the diff window", () -> ui(() -> diffs.openRequests().size() == 1));
        shot("2-diff");
        ui(() -> {
            diffs.click(first(diffs), DiffReview.ACCEPT);
            return null;
        });
        until("new.txt written", () -> Files.exists(folder.resolve("new.txt")));
        until("the diff closed", () -> ui(() -> diffs.openRequests().isEmpty()));
        until("the chat says Allowed", () -> driver(chat, "texts", ".permission .answer").contains("Allowed"));
        if (!"x = 2\n".equals(Files.readString(folder.resolve("new.txt")))) throw new AssertionError("new.txt has the wrong text");

        // Rejected there: nothing written.
        send(chat, "create other.txt");
        until("the second diff window", () -> ui(() -> diffs.openRequests().size() == 1));
        ui(() -> {
            diffs.click(first(diffs), DiffReview.REJECT);
            return null;
        });
        until("the chat says Denied", () -> driver(chat, "texts", ".permission .answer").contains("Denied"));
        until("the second diff closed", () -> ui(() -> diffs.openRequests().isEmpty()));
        if (Files.exists(folder.resolve("other.txt"))) throw new AssertionError("other.txt was written after Reject");

        // H15: the navigator, opened now, shows the turn.
        ui(() -> {
            NavigatorEditorProvider.open(p);
            return null;
        });
        until("the navigator page", () -> session.navigator() != null && session.navigator().isLoaded());
        Panel nav = session.navigator();
        until("the navigator's turn", () -> nav.evaluate("document.body.textContent").get(10, TimeUnit.SECONDS).contains("create other.txt"));
        shot("3-navigator");

        // H19: the page can't navigate away.
        String url = chat.evaluate("document.URL").get(10, TimeUnit.SECONDS);
        chat.evaluate("(location.href = 'https://example.com/', 'ok')").get(10, TimeUnit.SECONDS);
        Thread.sleep(1500);
        // The navigation is cancelled; the panel may load its own page again as a
        // safeguard, and while that runs the page doesn't answer.
        String[] last = {null};
        try {
            until("the chat page after a navigation attempt", () -> url.equals(last[0] = chat.evaluate("document.URL").get(10, TimeUnit.SECONDS)), 20);
        } catch (AssertionError e) {
            throw new AssertionError("the chat page navigated away to " + last[0], e);
        }
        until("the chat still works after it", () -> "1".equals(driver(chat, "count", "textarea")), 20);

        // H14: Ask About Selection (the editor's menu entry) fills in a prompt. The
        // navigator's tab is in front now: back to app.txt, as a right-click in it would.
        ui(() -> {
            VirtualFile vf = LocalFileSystem.getInstance().refreshAndFindFileByIoFile(app);
            Editor editor = null;
            for (FileEditor opened : FileEditorManager.getInstance(p).openFile(vf, true)) {
                if (opened instanceof TextEditor text) editor = text.getEditor();
            }
            if (editor == null) throw new AssertionError("app.txt has no text editor");
            if (!editor.getSelectionModel().hasSelection()) {
                editor.getSelectionModel().setSelection(0, editor.getDocument().getLineEndOffset(0));
            }
            AnAction action = ActionManager.getInstance().getAction("cmcoder.AskAboutSelection");
            DataContext data = SimpleDataContext.builder()
                    .add(CommonDataKeys.PROJECT, p)
                    .add(CommonDataKeys.EDITOR, editor)
                    .build();
            AnActionEvent event = AnActionEvent.createFromAnAction(action, null, ActionPlaces.EDITOR_POPUP, data);
            action.update(event);
            if (!event.getPresentation().isEnabledAndVisible()) throw new AssertionError("Ask About Selection is off in the editor");
            action.actionPerformed(event);
            return null;
        });
        until("the prompt", () -> chat.evaluate("document.querySelector('textarea').value").get(10, TimeUnit.SECONDS)
                .contains("Explain the selected code."));

        // H3: closing the chat stops cmcoder.
        ui(() -> {
            ToolWindow window = ToolWindowManager.getInstance(p).getToolWindow(Session.TOOL_WINDOW);
            Content content = window == null ? null : window.getContentManager().findContent(ChatToolWindowFactory.CHAT_TAB);
            if (content == null) throw new AssertionError("no chat tab in the tool window");
            window.getContentManager().removeContent(content, true);
            return null;
        });
        until("cmcoder stopped", () -> !session.running(), 30);
    }

    private static String first(DiffReview diffs) {
        Iterator<String> it = diffs.openRequests().iterator();
        if (!it.hasNext()) throw new AssertionError("no diff window");
        return it.next();
    }
}
