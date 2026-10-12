package cmcoder.idea;

import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import org.jetbrains.annotations.NotNull;

import com.intellij.ide.BrowserUtil;
import com.intellij.ide.impl.TrustedProjects;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.components.Service;
import com.intellij.openapi.editor.EditorFactory;
import com.intellij.openapi.editor.event.CaretEvent;
import com.intellij.openapi.editor.event.CaretListener;
import com.intellij.openapi.editor.event.SelectionEvent;
import com.intellij.openapi.editor.event.SelectionListener;
import com.intellij.openapi.fileEditor.FileEditorManagerEvent;
import com.intellij.openapi.fileEditor.FileEditorManagerListener;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowManager;
import com.intellij.ui.content.Content;

import cmcoder.ide.core.CodeSearch;
import cmcoder.ide.core.Host;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/**
 * A project's conversation: the shared {@link Host} plus what IntelliJ does for
 * it ({@link Ide}). One per open project; closing the project ends cmcoder
 * (H3). Every call into Host runs on one background thread, so the UI thread
 * and JCEF's threads never wait for cmcoder; Host may wait for the UI thread
 * (the editor context) without a deadlock.
 */
@Service(Service.Level.PROJECT)
public final class Session implements Disposable {
    /** The tool window's id in plugin.xml. */
    public static final String TOOL_WINDOW = "cmcoder";
    /** Lines of the log kept for the Log tab and Copy Diagnostics. */
    static final int LOG_LINES = 300;

    private final Project project;
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, Brand.product() + " session");
        t.setDaemon(true);
        return t;
    });
    private final Host.Config config = new Host.Config();
    private final Host host;
    private final DiffReview diffs;
    private volatile Panel chat;
    private volatile Panel navigator;
    private NavigatorEditorProvider.NavigatorFile navigatorFile;
    private volatile String lastState = "";
    private ScheduledFuture<?> pendingContext;
    private final CodeSearch codeSearch;
    private final List<Runnable> codeSearchViews = new CopyOnWriteArrayList<>();
    private final ArrayDeque<String> recentLog = new ArrayDeque<>();
    private final List<Consumer<String>> logViews = new CopyOnWriteArrayList<>();

    public Session(@NotNull Project project) {
        this.project = project;
        config.client = "jetbrains";
        config.product = Brand.product();
        host = new Host(new IntelliJIde(), config);
        codeSearch = new CodeSearch(host, cs -> codeSearchViews.forEach(Runnable::run));
        diffs = new DiffReview(project, this);
        listenToEditors();
    }

    public static Session get(@NotNull Project project) {
        return project.getService(Session.class);
    }

    /** Runs {@code action} on the session's thread, after refreshing the settings it starts cmcoder with. */
    public void run(Consumer<Host> action) {
        if (worker.isShutdown()) return;
        worker.execute(() -> {
            try {
                refreshConfig();
                action.accept(host);
            } catch (RuntimeException e) {
                Plugin.error(Brand.product() + ": " + e.getMessage(), e);
            }
        });
    }

    /** The settings and the project, read again before anything that may start cmcoder (H17). */
    private void refreshConfig() {
        Settings.Values s = Settings.values();
        config.pluginDir = Plugin.dir();
        String base = project.getBasePath();
        config.projectDir = base == null ? null : Path.of(base);
        config.programSetting = s.program == null || s.program.isBlank() ? null : s.program.trim();
        config.permissionMode = s.permissionMode == null || s.permissionMode.isBlank() ? null : s.permissionMode;
        config.trustProject = trusted();
    }

    /**
     * Whether the IDE trusts the project (H21): only then does cmcoder read the
     * project's own .cmcoder settings (--trust-project), as VS Code does with its
     * workspace trust.
     */
    boolean trusted() {
        return !project.isDisposed() && TrustedProjects.isTrusted(project);
    }

    /** Shows the chat's tool window (UI thread or not). */
    void showChat() {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (project.isDisposed()) return;
            ToolWindow window = ToolWindowManager.getInstance(project).getToolWindow(TOOL_WINDOW);
            if (window != null) window.activate(null);
        });
    }

    /** Shows the tool window's Log tab (UI thread or not). */
    void showLog() {
        ApplicationManager.getApplication().invokeLater(() -> {
            if (project.isDisposed()) return;
            ToolWindow window = ToolWindowManager.getInstance(project).getToolWindow(TOOL_WINDOW);
            if (window == null) return;
            window.activate(() -> {
                Content log = window.getContentManager().findContent(LogView.TAB);
                if (log != null) window.getContentManager().setSelectedContent(log);
            });
        });
    }

    void chatOpened(Panel panel) {
        chat = panel;
    }

    /** The navigator tab's file, the same one each time (UI thread). */
    NavigatorEditorProvider.NavigatorFile navigatorFile() {
        if (navigatorFile == null) navigatorFile = new NavigatorEditorProvider.NavigatorFile();
        return navigatorFile;
    }

    void navigatorOpened(Panel panel) {
        navigator = panel;
    }

    void navigatorClosed(Panel panel) {
        if (navigator == panel) navigator = null;
    }

    /** Code search (H16): its state and requests. */
    CodeSearch codeSearch() {
        return codeSearch;
    }

    /** {@code changed} runs (any thread) when code search's state changes; the returned action stops it. */
    Runnable onCodeSearchChange(Runnable changed) {
        codeSearchViews.add(changed);
        return () -> codeSearchViews.remove(changed);
    }

    // -- the log (H22) ------------------------------------------------------------------------

    /** A line for this project's log (cmcoder's stderr, starts and ends), also in the IDE's log. */
    void log(String line) {
        synchronized (recentLog) {
            recentLog.addLast(line);
            while (recentLog.size() > LOG_LINES) recentLog.removeFirst();
        }
        Plugin.log(line);
        for (Consumer<String> view : logViews) view.accept(line);
    }

    /** The last lines of the log (for the Log tab and Copy Diagnostics). */
    List<String> recentLog() {
        synchronized (recentLog) {
            return new ArrayList<>(recentLog);
        }
    }

    /** {@code view} gets each new line (any thread); the returned action stops it. */
    Runnable onLog(Consumer<String> view) {
        logViews.add(view);
        return () -> logViews.remove(view);
    }

    /** The chat closed: cmcoder stops with it (no hidden conversation keeps running). */
    void chatClosed(Panel panel) {
        if (chat != panel) return;
        chat = null;
        ApplicationManager.getApplication().invokeLater(diffs::closeAll);
        run(Host::dispose);
    }

    /** The diff viewer's buttons (H10). */
    void answer(String requestId, boolean allow, boolean remember) {
        run(h -> h.answer(requestId, allow, remember, null));
    }

    public String state() {
        return host.state();
    }

    /** The last state sent to the chat, with its message (for diagnostics). */
    public String lastState() {
        return lastState;
    }

    public boolean running() {
        return host.running();
    }

    @Override
    public void dispose() {
        worker.shutdownNow();
        host.dispose(); // on this thread: the worker is gone
    }

    // -- the editor's context follows the user (H7) -----------------------------------------------

    private void listenToEditors() {
        EditorFactory.getInstance().getEventMulticaster().addSelectionListener(new SelectionListener() {
            @Override
            public void selectionChanged(@NotNull SelectionEvent e) {
                if (e.getEditor().getProject() == project) contextChanged();
            }
        }, this);
        EditorFactory.getInstance().getEventMulticaster().addCaretListener(new CaretListener() {
            @Override
            public void caretPositionChanged(@NotNull CaretEvent e) {
                if (e.getEditor().getProject() == project) contextChanged();
            }
        }, this);
        project.getMessageBus().connect(this).subscribe(FileEditorManagerListener.FILE_EDITOR_MANAGER, new FileEditorManagerListener() {
            @Override
            public void selectionChanged(@NotNull FileEditorManagerEvent event) {
                contextChanged();
            }
        });
    }

    /** Many changes in a row (typing, moving the caret) refresh the label once. */
    private synchronized void contextChanged() {
        if (worker.isShutdown() || chat == null) return;
        if (pendingContext != null) pendingContext.cancel(false);
        pendingContext = worker.schedule(() -> {
            try {
                host.updateContext();
            } catch (RuntimeException e) {
                Plugin.error("Updating the editor context failed", e);
            }
        }, 300, TimeUnit.MILLISECONDS);
    }

    // -- what IntelliJ does for Host --------------------------------------------------------------

    private final class IntelliJIde implements Ide {
        @Override
        public void toPanel(String json) {
            if (json.startsWith("{\"kind\":\"state\"")) lastState = json;
            Panel p = chat;
            if (p != null) p.post(json);
        }

        @Override
        public void toNavigator(String json) {
            Panel p = navigator;
            if (p != null) p.post(json);
        }

        @Override
        public void openNavigator() {
            ApplicationManager.getApplication().invokeLater(() -> NavigatorEditorProvider.open(project));
        }

        @Override
        public void focusChat() {
            showChat();
        }

        @Override
        public Map<String, Object> editorContext() {
            return Workspace.editorContext(project);
        }

        @Override
        public boolean autoContext() {
            return Settings.values().autoContext;
        }

        @Override
        public boolean diffReview() {
            return Settings.values().diffReview;
        }

        @Override
        public void openDiff(String requestId, Protocol.FileChange change) {
            ApplicationManager.getApplication().invokeLater(() -> diffs.open(requestId, change));
        }

        @Override
        public void closeDiff(String requestId) {
            ApplicationManager.getApplication().invokeLater(() -> diffs.close(requestId));
        }

        @Override
        public List<String> ideTools() {
            return Workspace.IDE_TOOLS;
        }

        @Override
        public void runIdeTool(String name, Map<String, Object> input, ToolDone done) {
            Workspace.runIdeTool(project, name, input, config.projectDir, done);
        }

        @Override
        public void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen) {
            ApplicationManager.getApplication().invokeLater(
                    () -> Dialogs.pickRewind(project, points, c -> run(h -> chosen.accept(c))));
        }

        @Override
        public void attachFile(Consumer<String> chosen) {
            Path projectDir = config.projectDir;
            ApplicationManager.getApplication().invokeLater(
                    () -> Dialogs.attachFile(project, projectDir, path -> run(h -> chosen.accept(path))));
        }

        @Override
        public void openExternal(String url) {
            ApplicationManager.getApplication().invokeLater(() -> BrowserUtil.browse(url));
        }

        @Override
        public void log(String line) {
            Session.this.log(line);
        }
    }
}
