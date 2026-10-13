package cmcoder.netbeans;

import java.io.File;
import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.List;
import java.util.logging.Level;
import java.util.logging.Logger;

import org.openide.modules.InstalledFileLocator;
import org.openide.modules.ModuleInfo;
import org.openide.modules.Modules;
import org.openide.windows.IOProvider;
import org.openide.windows.InputOutput;

/** The module's files on disk, its log (an Output tab) and its version. */
public final class Plugin {
    public static final String CODE_NAME_BASE = "cmcoder.netbeans";
    private static final Logger LOG = Logger.getLogger(CODE_NAME_BASE);
    private static final int LOG_LINES = 300;
    private static final ArrayDeque<String> recent = new ArrayDeque<>();
    private static InputOutput io;

    private Plugin() {}

    /**
     * A file or folder the .nbm put next to the module (cmcoder-netbeans/...:
     * the chat page's files, bin/cmcoder/), or null if it isn't there.
     */
    public static Path file(String relative) {
        File f = InstalledFileLocator.getDefault().locate("cmcoder-netbeans/" + relative, CODE_NAME_BASE, false);
        return f == null ? null : f.toPath();
    }

    /** The folder that holds bin/cmcoder/ (the platform's copy of cmcoder), or null. */
    public static Path pluginDir() {
        Path bin = file("bin");
        return bin == null ? null : bin.getParent();
    }

    public static String version() {
        ModuleInfo m = Modules.getDefault().ownerOf(Plugin.class);
        return m == null ? "?" : String.valueOf(m.getSpecificationVersion());
    }

    /** A line for the log (cmcoder's stderr, starts and ends); also kept for Copy Diagnostics. */
    public static void log(String line) {
        synchronized (recent) {
            recent.addLast(line);
            while (recent.size() > LOG_LINES) recent.removeFirst();
        }
        try {
            io().getOut().println(line);
        } catch (RuntimeException e) {
            // no Output window (closing down): the recent lines are still kept
        }
    }

    public static List<String> recentLog() {
        synchronized (recent) {
            return new ArrayList<>(recent);
        }
    }

    /** Shows the log in the Output window. */
    public static void showLog() {
        io().select();
    }

    private static synchronized InputOutput io() {
        if (io == null || io.isClosed()) io = IOProvider.getDefault().getIO(Brand.product() + " log", false);
        return io;
    }

    public static void error(String message, Throwable e) {
        LOG.log(Level.WARNING, message, e);
    }
}
