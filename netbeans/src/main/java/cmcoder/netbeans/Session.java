package cmcoder.netbeans;

import java.beans.PropertyChangeListener;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.ScheduledFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import javax.swing.SwingUtilities;
import javax.swing.event.CaretListener;
import javax.swing.text.JTextComponent;

import org.netbeans.api.editor.EditorRegistry;

import cmcoder.ide.core.CodeSearch;
import cmcoder.ide.core.Host;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/**
 * The plugin's one conversation: the shared {@link Host} plus what NetBeans
 * does for it ({@link Ide}). Every call into Host runs on one background
 * thread, so the Swing and JavaFX threads never wait for cmcoder, and Host may
 * wait for the Swing thread (the editor context) without a deadlock.
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
    private final List<Runnable> unhook = new CopyOnWriteArrayList<>();

    private final CodeSearch codeSearch;
    private final List<Runnable> codeSearchViews = new CopyOnWriteArrayList<>();
    private volatile String lastState = "";

    private Session() {
        config.client = "netbeans";
        config.product = Brand.product();
        host = new Host(new NetBeansIde(), config);
        codeSearch = new CodeSearch(host, cs -> codeSearchViews.forEach(Runnable::run));
    }

    /** The session (made on first use). */
    public static synchronized Session get() {
        if (current == null) current = new Session();
        return current;
    }

    /** NetBeans (or the module) stops: end cmcoder (H3). */
    public static synchronized void disposeAll() {
        if (current == null) return;
        Session s = current;
        current = null;
        s.unhookListeners();
        s.worker.shutdownNow();
        s.host.dispose(); // on this thread: the worker is gone
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

    // -- calls into Host, on the worker -----------------------------------------------------

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

    /** Settings and the project, read again before anything that may start cmcoder (H17). */
    private void refreshConfig() {
        config.programSetting = Options.program();
        config.pluginDir = Plugin.pluginDir();
        config.permissionMode = Options.permissionMode();
        config.trustProject = Options.trustProject();
        config.projectDir = Workspace.onUi(Workspace::projectDir);
    }

    // -- the windows ------------------------------------------------------------------------

    void chatOpened(Panel panel) {
        chat = panel;
        hookListeners();
    }

    /** The chat closed: cmcoder stops with it (no hidden conversation keeps running). */
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

    Panel chat() {
        return chat;
    }

    Panel navigator() {
        return navigator;
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

    /** Tests: extra environment for cmcoder (the mock model server, a private settings folder). */
    void testEnvironment(Map<String, String> env) {
        config.env = env;
    }

    DiffReview diffs() {
        return diffs;
    }

    // -- the editor's context follows the user (H7) -----------------------------------------------

    private void hookListeners() {
        unhookListeners();
        CaretListener caret = e -> contextChanged();
        PropertyChangeListener focus = e -> {
            Object old = e.getOldValue();
            Object now = e.getNewValue();
            if (old instanceof JTextComponent) ((JTextComponent) old).removeCaretListener(caret);
            if (now instanceof JTextComponent) ((JTextComponent) now).addCaretListener(caret);
            contextChanged();
        };
        SwingUtilities.invokeLater(() -> {
            EditorRegistry.addPropertyChangeListener(focus);
            JTextComponent last = EditorRegistry.lastFocusedComponent();
            if (last != null) last.addCaretListener(caret);
            unhook.add(() -> {
                EditorRegistry.removePropertyChangeListener(focus);
                JTextComponent c = EditorRegistry.lastFocusedComponent();
                if (c != null) c.removeCaretListener(caret);
            });
        });
    }

    private void unhookListeners() {
        for (Runnable r : unhook) {
            SwingUtilities.invokeLater(() -> {
                try {
                    r.run();
                } catch (RuntimeException e) {
                    // already gone
                }
            });
        }
        unhook.clear();
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

    // -- what NetBeans does for Host --------------------------------------------------------------

    private final class NetBeansIde implements Ide {
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
            SwingUtilities.invokeLater(NavigatorWindow::showIt);
        }

        @Override
        public void focusChat() {
            SwingUtilities.invokeLater(ChatWindow::showIt);
        }

        @Override
        public Map<String, Object> editorContext() {
            return Workspace.editorContext();
        }

        @Override
        public boolean autoContext() {
            return Options.autoContext();
        }

        @Override
        public boolean diffReview() {
            return Options.diffReview();
        }

        @Override
        public void openDiff(String requestId, Protocol.FileChange change) {
            SwingUtilities.invokeLater(() -> diffs.open(requestId, change));
        }

        @Override
        public void closeDiff(String requestId) {
            SwingUtilities.invokeLater(() -> diffs.close(requestId));
        }

        @Override
        public List<String> ideTools() {
            return Workspace.IDE_TOOLS;
        }

        @Override
        public void runIdeTool(String name, Map<String, Object> input, ToolDone done) {
            Workspace.runIdeTool(name, input, config.projectDir, done);
        }

        @Override
        public void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen) {
            SwingUtilities.invokeLater(() -> Dialogs.pickRewind(points, c -> run(h -> chosen.accept(c))));
        }

        @Override
        public void attachFile(Consumer<String> chosen) {
            java.nio.file.Path project = config.projectDir;
            SwingUtilities.invokeLater(() -> Dialogs.attachFile(project, path -> run(h -> chosen.accept(path))));
        }

        @Override
        public void openExternal(String url) {
            SwingUtilities.invokeLater(() -> Dialogs.openExternal(url));
        }

        @Override
        public void log(String line) {
            Plugin.log(line);
        }
    }

    /** The diff viewer's buttons (H10). */
    void answer(String requestId, boolean allow, boolean remember) {
        run(h -> h.answer(requestId, allow, remember, null));
    }

    /** For tests: nothing pending on the session's thread. */
    public void drain() throws InterruptedException {
        CountDownLatch done = new CountDownLatch(1);
        worker.execute(done::countDown);
        done.await(10, TimeUnit.SECONDS);
    }
}
