package cmcoder.eclipse;

import java.io.IOException;
import java.net.URISyntaxException;
import java.net.URL;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;

import org.eclipse.core.runtime.FileLocator;
import org.eclipse.core.runtime.ILog;
import org.eclipse.core.runtime.Status;
import org.eclipse.ui.console.ConsolePlugin;
import org.eclipse.ui.console.IConsole;
import org.eclipse.ui.console.MessageConsole;
import org.eclipse.ui.console.MessageConsoleStream;
import org.eclipse.ui.plugin.AbstractUIPlugin;
import org.osgi.framework.BundleContext;

/** The plugin: its log (a console), its files, and the session with cmcoder. */
public final class Activator extends AbstractUIPlugin {
    public static final String ID = "cmcoder.eclipse";
    private static final int LOG_LINES = 300;
    private static Activator plugin;

    private final ArrayDeque<String> recent = new ArrayDeque<>();
    private MessageConsole console;
    private MessageConsoleStream out;

    public static Activator get() {
        return plugin;
    }

    @Override
    public void start(BundleContext context) throws Exception {
        super.start(context);
        plugin = this;
    }

    @Override
    public void stop(BundleContext context) throws Exception {
        Session.disposeAll();
        plugin = null;
        super.stop(context);
    }

    /** A line for the cmcoder console (cmcoder's stderr, starts and ends). */
    public synchronized void log(String line) {
        recent.addLast(line);
        while (recent.size() > LOG_LINES) recent.removeFirst();
        try {
            stream().println(line);
        } catch (RuntimeException e) {
            // no console (closing down): the recent lines are still kept
        }
    }

    public synchronized List<String> recentLog() {
        return new ArrayList<>(recent);
    }

    public synchronized MessageConsole console() {
        if (console == null) {
            console = new MessageConsole(Brand.product() + " log", null);
            ConsolePlugin.getDefault().getConsoleManager().addConsoles(new IConsole[] {console});
        }
        return console;
    }

    private MessageConsoleStream stream() {
        if (out == null) out = console().newMessageStream();
        return out;
    }

    public static void error(String message, Throwable e) {
        ILog.of(Activator.class).log(Status.error(message, e));
    }

    /**
     * A folder of this plugin on disk (the bundle is installed as a folder:
     * Eclipse-BundleShape: dir), or null if it isn't there.
     */
    public static Path file(String relative) {
        try {
            URL url = FileLocator.find(get().getBundle(), new org.eclipse.core.runtime.Path(relative), null);
            if (url == null) return null;
            URL local = FileLocator.toFileURL(url);
            return Paths.get(new java.net.URI(local.getProtocol(), local.getHost(), local.getPath(), null));
        } catch (IOException | URISyntaxException e) {
            return null;
        }
    }

    /** The folder that holds bin/cmcoder/ (the platform fragment's copy of cmcoder), or null. */
    public static Path pluginDir() {
        Path bin = file("bin");
        return bin == null ? null : bin.getParent();
    }

    public String version() {
        return getBundle().getVersion().toString();
    }
}
