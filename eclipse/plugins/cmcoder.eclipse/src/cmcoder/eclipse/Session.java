package cmcoder.eclipse;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import org.eclipse.core.resources.IResourceChangeEvent;
import org.eclipse.core.resources.IResourceChangeListener;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IPartListener2;
import org.eclipse.ui.ISelectionListener;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchPartReference;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.PlatformUI;

import cmcoder.ide.core.CodeSearch;
import cmcoder.ide.core.Host;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/**
 * The plugin's one conversation: the shared {@link Host} plus what Eclipse does
 * for it ({@link Ide}). Every call into Host runs on one background thread, so
 * the UI thread never waits for cmcoder, and Host may wait for the UI thread
 * (the editor context) without a deadlock.
 */
public final class Session {
    private static Session current;

    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, Brand.product() + " session");
        t.setDaemon(true);
        return t;
    });
    private final Host.Config config = new Host.Config();
    private final Host host;
    private final DiffReview diffs = new DiffReview(this);
    private volatile Panel chat;
    private volatile Panel navigator;
    private ScheduledFuture<?> pendingContext;
    private final List<Runnable> unhook = new ArrayList<>();

    private final CodeSearch codeSearch;
    private final List<Runnable> codeSearchViews = new java.util.concurrent.CopyOnWriteArrayList<>();

    private Session() {
        config.client = "eclipse";
        config.product = Brand.product();
        host = new Host(new EclipseIde(), config);
        codeSearch = new CodeSearch(host, cs -> codeSearchViews.forEach(Runnable::run));
    }

    /** Code search (H16): its state and requests. */
    public CodeSearch codeSearch() {
        return codeSearch;
    }

    /** {@code changed} runs (any thread) when code search's state changes; the returned action stops it. */
    Runnable onCodeSearchChange(Runnable changed) {
        codeSearchViews.add(changed);
        return () -> codeSearchViews.remove(changed);
    }

    /** The session (made on first use). */
    public static synchronized Session get() {
        if (current == null) current = new Session();
        return current;
    }

    /** Eclipse (or the plugin) stops: end cmcoder (H3). */
    public static synchronized void disposeAll() {
        if (current == null) return;
        Session s = current;
        current = null;
        s.unhookListeners();
        s.worker.shutdownNow();
        s.host.dispose(); // on this thread: the worker may be gone
    }

    // -- calls into Host, on the worker ---------------------------------------------

    /** Runs {@code action} on the session's thread, after refreshing the settings it starts cmcoder with. */
    public void run(Consumer<Host> action) {
        if (worker.isShutdown()) return;
        worker.execute(() -> {
            try {
                refreshConfig();
                action.accept(host);
            } catch (RuntimeException e) {
                Activator.error(Brand.product() + ": " + e.getMessage(), e);
            }
        });
    }

    /** Settings and the project, read again before anything that may start cmcoder (H17). */
    private void refreshConfig() {
        String program = Preferences.store().getString(Preferences.PROGRAM);
        config.programSetting = program == null || program.isBlank() ? null : program.trim();
        config.pluginDir = Activator.pluginDir();
        String mode = Preferences.store().getString(Preferences.PERMISSION_MODE);
        config.permissionMode = mode == null || mode.isBlank() ? null : mode;
        config.trustProject = Preferences.store().getBoolean(Preferences.TRUST_PROJECT);
        config.projectDir = Workspace.onUi(Workspace::projectDir);
    }

    // -- the views ------------------------------------------------------------------

    void chatOpened(Panel panel, IWorkbenchWindow window) {
        chat = panel;
        hookListeners(window);
    }

    /** The chat view closed: cmcoder stops with it (no hidden conversation keeps running). */
    void chatClosed(Panel panel) {
        if (chat != panel) return;
        chat = null;
        unhookListeners();
        run(Host::dispose);
    }

    void navigatorOpened(Panel panel) {
        navigator = panel;
    }

    void navigatorClosed(Panel panel) {
        if (navigator == panel) navigator = null;
    }

    public String state() {
        return host.state();
    }

    private volatile String lastState = "";

    /** The last state sent to the chat, with its message (for diagnostics). */
    public String lastState() {
        return lastState;
    }

    public boolean running() {
        return host.running();
    }

    /** Tests: extra environment for cmcoder (the mock model server, a private settings folder). */
    void testEnvironment(Map<String, String> env) {
        config.env = env;
    }

    DiffReview diffs() {
        return diffs;
    }

    // -- the editor's context follows the user (H7) -------------------------------------

    private synchronized void hookListeners(IWorkbenchWindow window) {
        unhookListeners();
        ISelectionListener selection = (part, sel) -> contextChanged();
        window.getSelectionService().addPostSelectionListener(selection);
        unhook.add(() -> window.getSelectionService().removePostSelectionListener(selection));
        IPartListener2 parts = new IPartListener2() {
            @Override
            public void partActivated(IWorkbenchPartReference ref) {
                contextChanged();
            }

            @Override
            public void partClosed(IWorkbenchPartReference ref) {
                contextChanged();
            }
        };
        window.getPartService().addPartListener(parts);
        unhook.add(() -> window.getPartService().removePartListener(parts));
        IResourceChangeListener markers = (IResourceChangeEvent e) -> contextChanged();
        ResourcesPlugin.getWorkspace().addResourceChangeListener(markers, IResourceChangeEvent.POST_BUILD);
        unhook.add(() -> ResourcesPlugin.getWorkspace().removeResourceChangeListener(markers));
    }

    private synchronized void unhookListeners() {
        for (Runnable r : unhook) {
            try {
                r.run();
            } catch (RuntimeException e) {
                // the window is already gone
            }
        }
        unhook.clear();
    }

    /** Many changes in a row (typing a selection, a build) refresh the label once. */
    private synchronized void contextChanged() {
        if (worker.isShutdown() || chat == null) return;
        if (pendingContext != null) pendingContext.cancel(false);
        pendingContext = worker.schedule(() -> {
            try {
                host.updateContext();
            } catch (RuntimeException e) {
                Activator.error("Updating the editor context failed", e);
            }
        }, 300, TimeUnit.MILLISECONDS);
    }

    // -- views by id ----------------------------------------------------------------------

    static final String CHAT_VIEW = "cmcoder.eclipse.chat";
    static final String NAVIGATOR_VIEW = "cmcoder.eclipse.navigator";

    /** Shows a view (UI thread). */
    static void showView(String id, int mode) {
        IWorkbenchWindow window = PlatformUI.getWorkbench().getActiveWorkbenchWindow();
        if (window == null) {
            IWorkbenchWindow[] all = PlatformUI.getWorkbench().getWorkbenchWindows();
            if (all.length == 0) return;
            window = all[0];
        }
        IWorkbenchPage page = window.getActivePage();
        if (page == null) return;
        try {
            page.showView(id, null, mode);
        } catch (PartInitException e) {
            Activator.error("Could not open " + id, e);
        }
    }

    private static void ui(Runnable r) {
        Display display = PlatformUI.isWorkbenchRunning() ? PlatformUI.getWorkbench().getDisplay() : null;
        if (display != null && !display.isDisposed()) display.asyncExec(r);
    }

    // -- what Eclipse does for Host ----------------------------------------------------------

    private final class EclipseIde implements Ide {
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
            ui(() -> showView(NAVIGATOR_VIEW, IWorkbenchPage.VIEW_ACTIVATE));
        }

        @Override
        public void focusChat() {
            ui(() -> showView(CHAT_VIEW, IWorkbenchPage.VIEW_ACTIVATE));
        }

        @Override
        public Map<String, Object> editorContext() {
            return Workspace.onUi(Workspace::editorContext);
        }

        @Override
        public boolean autoContext() {
            return Preferences.store().getBoolean(Preferences.AUTO_CONTEXT);
        }

        @Override
        public boolean diffReview() {
            return Preferences.store().getBoolean(Preferences.DIFF_REVIEW);
        }

        @Override
        public void openDiff(String requestId, Protocol.FileChange change) {
            ui(() -> diffs.open(requestId, change));
        }

        @Override
        public void closeDiff(String requestId) {
            ui(() -> diffs.close(requestId));
        }

        @Override
        public List<String> ideTools() {
            return Workspace.IDE_TOOLS;
        }

        @Override
        public void runIdeTool(String name, Map<String, Object> input, ToolDone done) {
            Workspace.runIdeTool(name, input, done);
        }

        @Override
        public void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen) {
            ui(() -> Dialogs.pickRewind(points, c -> run(h -> chosen.accept(c))));
        }

        @Override
        public void attachFile(Consumer<String> chosen) {
            ui(() -> Dialogs.attachFile(config.projectDir, path -> run(h -> chosen.accept(path))));
        }

        @Override
        public void openExternal(String url) {
            ui(() -> Dialogs.openExternal(url));
        }

        @Override
        public void log(String line) {
            Activator a = Activator.get();
            if (a != null) a.log(line);
        }
    }

    /** The diff viewer's buttons (H10). */
    void answer(String requestId, boolean allow, boolean remember) {
        run(h -> h.answer(requestId, allow, remember, null));
    }

    /** For tests: nothing pending on the session's thread. */
    public void drain() throws InterruptedException {
        java.util.concurrent.CountDownLatch done = new java.util.concurrent.CountDownLatch(1);
        worker.execute(done::countDown);
        done.await(10, TimeUnit.SECONDS);
    }
}
