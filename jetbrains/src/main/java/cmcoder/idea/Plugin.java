package cmcoder.idea;

import java.io.IOException;
import java.net.JarURLConnection;
import java.net.URISyntaxException;
import java.net.URL;
import java.net.URLConnection;
import java.nio.file.FileSystemNotFoundException;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;

import com.intellij.openapi.diagnostic.Logger;

/** The plugin's folder and log. */
public final class Plugin {
    /** The id in plugin.xml. */
    public static final String ID = "cmcoder.idea";
    private static final Logger LOG = Logger.getInstance("cmcoder");
    private static volatile Path dir;

    private Plugin() {}

    /**
     * Where the IDE unpacked the plugin: lib/ (the code), panel/ (the chat's
     * files), bin/cmcoder/ (the program). Found from the plugin's own jar, in
     * lib/: the plugin manager's lookups are internal API in newer releases.
     */
    public static Path dir() {
        Path found = dir;
        if (found == null) {
            found = fromJar();
            dir = found;
        }
        return found;
    }

    private static Path fromJar() {
        URL url = Plugin.class.getResource("Plugin.class");
        if (url == null) return null;
        try {
            URLConnection connection = url.openConnection(); // not connected: nothing is read
            if (!(connection instanceof JarURLConnection)) return null;
            Path jar = toPath(((JarURLConnection) connection).getJarFileURL());
            Path lib = jar == null ? null : jar.getParent();
            return lib == null ? null : lib.getParent();
        } catch (IOException | RuntimeException e) {
            error("Could not find the plugin's folder from " + url, e);
            return null;
        }
    }

    private static Path toPath(URL url) {
        try {
            return Path.of(url.toURI());
        } catch (URISyntaxException | IllegalArgumentException | FileSystemNotFoundException e) {
            // An address with unescaped characters (a space): its path as written.
            String path = url.getPath();
            if (path.matches("^/[A-Za-z]:/.*")) path = path.substring(1); // /C:/... on Windows
            try {
                return Path.of(path);
            } catch (InvalidPathException ignored) {
                return null;
            }
        }
    }

    /** A file of the plugin, or null when it isn't there. */
    public static Path file(String relative) {
        Path folder = dir();
        if (folder == null) return null;
        Path file = folder.resolve(relative);
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
