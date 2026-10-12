package cmcoder.idea;

import java.nio.file.Files;
import java.nio.file.Path;

import com.intellij.ide.plugins.IdeaPluginDescriptor;
import com.intellij.ide.plugins.PluginManagerCore;
import com.intellij.openapi.diagnostic.Logger;
import com.intellij.openapi.extensions.PluginId;

/** The plugin's folder and log. */
public final class Plugin {
    /** The id in plugin.xml. */
    public static final String ID = "cmcoder.idea";
    private static final Logger LOG = Logger.getInstance("cmcoder");

    private Plugin() {}

    /** Where the IDE unpacked the plugin: lib/ (the code), panel/ (the chat's files), bin/cmcoder/ (the program). */
    public static Path dir() {
        IdeaPluginDescriptor plugin = PluginManagerCore.getPlugin(PluginId.getId(ID));
        return plugin == null ? null : plugin.getPluginPath();
    }

    /** A file of the plugin, or null when it isn't there. */
    public static Path file(String relative) {
        Path dir = dir();
        if (dir == null) return null;
        Path file = dir.resolve(relative);
        return Files.exists(file) ? file : null;
    }

    /** A line for the IDE's log (cmcoder's stderr, starts and ends). */
    public static void log(String line) {
        LOG.info(line);
    }

    public static void error(String what, Throwable e) {
        LOG.warn(what, e);
    }
}
