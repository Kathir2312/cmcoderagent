package cmcoder.idea;

import java.nio.file.Path;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.function.Consumer;

import org.jetbrains.annotations.NotNull;

import com.intellij.ide.BrowserUtil;
import com.intellij.openapi.Disposable;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.components.Service;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowManager;

import cmcoder.ide.core.Host;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/**
 * A project's conversation: the shared {@link Host} plus what IntelliJ does for
 * it ({@link Ide}). One per open project; closing the project ends cmcoder
 * (H3). Every call into Host runs on one background thread, so the UI thread
 * and JCEF's threads never wait for cmcoder.
 */
@Service(Service.Level.PROJECT)
public final class Session implements Disposable {
    /** The tool window's id in plugin.xml. */
    public static final String TOOL_WINDOW = "cmcoder";

    private final Project project;
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, Brand.product() + " session");
        t.setDaemon(true);
        return t;
    });
    private final Host.Config config = new Host.Config();
    private final Host host;
    private volatile Panel chat;
    private volatile String lastState = "";

    public Session(@NotNull Project project) {
        this.project = project;
        config.client = "jetbrains";
        config.product = Brand.product();
        host = new Host(new IntelliJIde(), config);
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

    /** The project and the plugin's folder, read again before anything that may start cmcoder. */
    private void refreshConfig() {
        config.pluginDir = Plugin.dir();
        String base = project.getBasePath();
        config.projectDir = base == null ? null : Path.of(base);
        // Item 4: the settings page (program, permission mode) and the IDE's trusted projects.
        config.programSetting = null;
        config.permissionMode = null;
        config.trustProject = false;
    }

    void chatOpened(Panel panel) {
        chat = panel;
    }

    /** The chat closed: cmcoder stops with it (no hidden conversation keeps running). */
    void chatClosed(Panel panel) {
        if (chat != panel) return;
        chat = null;
        run(Host::dispose);
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
            // Item 4: the Agent Navigator tab.
        }

        @Override
        public void openNavigator() {
            // Item 4: the Agent Navigator tab.
        }

        @Override
        public void focusChat() {
            ApplicationManager.getApplication().invokeLater(() -> {
                if (project.isDisposed()) return;
                ToolWindow window = ToolWindowManager.getInstance(project).getToolWindow(TOOL_WINDOW);
                if (window != null) window.activate(null);
            });
        }

        @Override
        public Map<String, Object> editorContext() {
            return null; // item 3
        }

        @Override
        public boolean autoContext() {
            return false; // item 3
        }

        @Override
        public boolean diffReview() {
            return false; // item 3: changes are answered in the chat until then
        }

        @Override
        public void openDiff(String requestId, Protocol.FileChange change) {
            // item 3
        }

        @Override
        public void closeDiff(String requestId) {
            // item 3
        }

        @Override
        public List<String> ideTools() {
            return List.of(); // item 3
        }

        @Override
        public void runIdeTool(String name, Map<String, Object> input, ToolDone done) {
            done.done("This IDE tool isn't available yet.", true);
        }

        @Override
        public void pickRewind(List<Protocol.RewindPoint> points, Consumer<RewindChoice> chosen) {
            // item 3
        }

        @Override
        public void attachFile(Consumer<String> chosen) {
            // item 3
        }

        @Override
        public void openExternal(String url) {
            ApplicationManager.getApplication().invokeLater(() -> BrowserUtil.browse(url));
        }

        @Override
        public void log(String line) {
            Plugin.log(line);
        }
    }
}
