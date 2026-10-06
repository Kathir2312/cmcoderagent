package cmcoder.ide.core;

import java.io.File;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.TimeUnit;

/**
 * Which cmcoder program to start (host duty H1): the user's own setting, else
 * the copy bundled in the plugin, else {@code cmcoder} on PATH. Never Python or
 * uv (developers' PCs may have neither).
 *
 * <p>A project must not be able to choose the program: the setting is the
 * user's (an IDE-wide setting, never a project file) and must be an absolute
 * path, and PATH is searched without its relative entries and without the
 * project folder (on Windows a bare program name is otherwise looked for in the
 * current folder first, so a cloned repository could ship its own
 * cmcoder.exe).
 */
public final class ProgramLocator {
    private ProgramLocator() {}

    public static boolean windows() {
        return System.getProperty("os.name", "").toLowerCase(Locale.ROOT).startsWith("windows");
    }

    /** Where the plugin keeps its copy: {@code <pluginDir>/bin/cmcoder/cmcoder[.exe]}. */
    public static Path bundled(Path pluginDir) {
        return pluginDir.resolve("bin").resolve("cmcoder").resolve(windows() ? "cmcoder.exe" : "cmcoder");
    }

    /** The result of a lookup: the program, or why there is none. */
    public static final class Found {
        public final Path program;
        public final String problem;

        Found(Path program, String problem) {
            this.program = program;
            this.problem = problem;
        }
    }

    /**
     * @param setting   the user's setting (may be null or empty)
     * @param pluginDir the plugin's folder (may be null)
     * @param projectDir the project folder (never searched)
     * @param env       the environment to read PATH (and PATHEXT) from
     */
    public static Found find(String setting, Path pluginDir, Path projectDir, Map<String, String> env) {
        if (setting != null && !setting.trim().isEmpty()) {
            Path p = Paths.get(setting.trim());
            if (!p.isAbsolute()) {
                return new Found(null, "The cmcoder program setting must be a full path (it is \"" + setting.trim() + "\").");
            }
            if (batch(p.toString())) {
                return new Found(null, "The cmcoder program setting points to a batch file; set it to cmcoder.exe itself.");
            }
            return Files.isRegularFile(p)
                    ? new Found(p, null)
                    : new Found(null, "The cmcoder program set in the settings doesn't exist: " + p);
        }
        if (pluginDir != null) {
            Path b = bundled(pluginDir);
            if (Files.isRegularFile(b)) return new Found(b, null);
        }
        Path onPath = onPath("cmcoder", projectDir, env);
        if (onPath != null) return new Found(onPath, null);
        return new Found(null, "cmcoder wasn't found: this plugin file should contain it. Install the plugin file for "
                + "your platform (win32-x64, linux-x64 or darwin-arm64), or set the cmcoder program's full path in the settings.");
    }

    /** A program on PATH, skipping relative entries and the project folder; null if none. */
    public static Path onPath(String name, Path projectDir, Map<String, String> env) {
        boolean win = windows();
        String path = null;
        for (Map.Entry<String, String> e : env.entrySet()) {
            if (e.getKey().equalsIgnoreCase("PATH")) {
                path = e.getValue();
                break;
            }
        }
        if (path == null) return null;
        Path here = projectDir == null ? null : projectDir.toAbsolutePath().normalize();
        List<String> exts = new ArrayList<>();
        if (win) {
            String pathext = env.getOrDefault("PATHEXT", ".COM;.EXE;.BAT;.CMD");
            String lower = name.toLowerCase(Locale.ROOT);
            boolean named = false;
            for (String e : pathext.split(";")) {
                if (!e.isEmpty() && lower.endsWith(e.toLowerCase(Locale.ROOT))) named = true;
            }
            if (named) {
                exts.add("");
            } else {
                for (String e : pathext.split(";")) if (!e.isEmpty()) exts.add(e.toLowerCase(Locale.ROOT));
            }
        } else {
            exts.add("");
        }
        for (String dir : path.split(File.pathSeparator)) {
            if (dir.isEmpty()) continue;
            Path d = Paths.get(dir);
            if (!d.isAbsolute()) continue;
            Path norm = d.toAbsolutePath().normalize();
            if (here != null && (win ? norm.toString().equalsIgnoreCase(here.toString()) : norm.equals(here))) continue;
            for (String ext : exts) {
                // Batch files run through cmd.exe and its own argument rules: never those.
                if (batch(name + ext)) continue;
                Path candidate = d.resolve(name + ext);
                if (Files.isRegularFile(candidate) && (win || Files.isExecutable(candidate))) return candidate;
            }
        }
        return null;
    }

    static boolean batch(String file) {
        String lower = file.toLowerCase(Locale.ROOT);
        return lower.endsWith(".bat") || lower.endsWith(".cmd");
    }

    /**
     * Gets the bundled program ready to run after the IDE unpacked the plugin:
     * zip installers can drop the executable bit (macOS, Linux), and macOS
     * quarantines downloaded files, which stops an unsigned program from
     * starting. Harmless when there is nothing to do.
     */
    public static void prepare(Path program) {
        if (windows() || program == null || !Files.isRegularFile(program)) return;
        File f = program.toFile();
        if (!f.canExecute()) {
            // The program, and nothing else in its folder needs the bit.
            if (!f.setExecutable(true, false)) f.setExecutable(true, true);
        }
        if (System.getProperty("os.name", "").toLowerCase(Locale.ROOT).contains("mac")) {
            Path folder = program.getParent();
            try {
                Process p = new ProcessBuilder("/usr/bin/xattr", "-dr", "com.apple.quarantine", folder.toString())
                        .redirectErrorStream(true)
                        .start();
                p.getInputStream().readAllBytes();
                p.waitFor(30, TimeUnit.SECONDS);
            } catch (IOException e) {
                // xattr missing: nothing more to do; a quarantined program reports itself when started
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
    }
}
