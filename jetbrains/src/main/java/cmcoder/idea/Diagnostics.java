package cmcoder.idea;

import java.awt.datatransfer.StringSelection;
import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Properties;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.jetbrains.annotations.NotNull;

import com.intellij.notification.NotificationGroupManager;
import com.intellij.notification.NotificationType;
import com.intellij.openapi.application.ApplicationInfo;
import com.intellij.openapi.application.PathManager;
import com.intellij.openapi.ide.CopyPasteManager;
import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.ProgressManager;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;

import cmcoder.ide.core.ProgramLocator;

/**
 * Copy Diagnostics (H22): what support needs, to the clipboard; nothing is
 * sent anywhere, and cmcoder masks keys in what it prints. Runs cmcoder
 * (version, doctor) in the background, so the UI doesn't wait.
 */
final class Diagnostics {
    /** The notification group's id in plugin.xml. */
    static final String NOTIFICATIONS = "cmcoder";
    private static volatile String version;

    private Diagnostics() {}

    static void copy(Project project) {
        ProgressManager.getInstance().run(new Task.Backgroundable(project, Brand.product() + ": collecting diagnostics", false) {
            private String text;

            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                text = collect(project, true);
            }

            @Override
            public void onSuccess() {
                CopyPasteManager.getInstance().setContents(new StringSelection(text));
                NotificationGroupManager.getInstance().getNotificationGroup(NOTIFICATIONS)
                        .createNotification("The " + Brand.product() + " diagnostics are on the clipboard. Read them before you share them.",
                                NotificationType.INFORMATION)
                        .notify(project);
            }
        });
    }

    static String collect(Project project, boolean runProgram) {
        Session session = Session.get(project);
        Settings.Values settings = Settings.values();
        String base = project.getBasePath();
        Path projectDir = base == null ? null : Path.of(base);
        String setting = settings.program == null || settings.program.isBlank() ? null : settings.program.trim();
        StringBuilder s = new StringBuilder();
        ApplicationInfo ide = ApplicationInfo.getInstance();
        s.append(Brand.product()).append(" for IntelliJ ").append(version()).append('\n');
        s.append("IDE: ").append(ide.getFullApplicationName()).append(" (").append(ide.getBuild().asString()).append(")\n");
        s.append("IDE system folder: ").append(PathManager.getSystemPath()).append('\n');
        s.append("OS: ").append(System.getProperty("os.name")).append(' ').append(System.getProperty("os.version"))
                .append(' ').append(System.getProperty("os.arch")).append('\n');
        s.append("Java: ").append(System.getProperty("java.version")).append(' ').append(System.getProperty("java.vendor")).append('\n');
        s.append("JCEF: ").append(Plugin.jcef() ? "yes" : "no").append('\n');
        s.append("Plugin folder: ").append(Plugin.dir()).append('\n');
        s.append("Trusted project: ").append(session.trusted() ? "yes" : "no").append('\n');
        s.append("Settings: program=").append(setting == null ? "(the plugin's)" : setting)
                .append(", permissionMode=").append(settings.permissionMode == null || settings.permissionMode.isEmpty() ? "(default)" : settings.permissionMode)
                .append(", autoContext=").append(settings.autoContext)
                .append(", diffReview=").append(settings.diffReview).append('\n');
        ProgramLocator.Found found = ProgramLocator.find(setting, Plugin.dir(), projectDir, new HashMap<>(System.getenv()));
        s.append("Program: ").append(found.program != null ? found.program : "not found: " + found.problem).append('\n');
        if (runProgram && found.program != null) {
            s.append("Version: ").append(output(found.program, projectDir, "--version").trim()).append('\n');
            s.append("\ncmcoder doctor --no-probe:\n").append(output(found.program, projectDir, "doctor", "--no-probe")).append('\n');
        }
        s.append("State: ").append(session.state()).append('\n');
        s.append("Last state message: ").append(session.lastState()).append('\n');
        s.append("\nRecent log:\n");
        for (String line : session.recentLog()) s.append(line).append('\n');
        return s.toString();
    }

    /** The plugin's version (written by the build into its jar). */
    static String version() {
        String v = version;
        if (v == null) {
            v = "?";
            try (InputStream in = Diagnostics.class.getResourceAsStream("/cmcoder/idea/build.properties")) {
                if (in != null) {
                    Properties p = new Properties();
                    p.load(in);
                    v = p.getProperty("version", "?");
                }
            } catch (IOException e) {
                // unknown
            }
            version = v;
        }
        return v;
    }

    /** What the program prints (stdout and stderr), at most 30 s; no shell. */
    private static String output(Path program, Path project, String... args) {
        List<String> command = new ArrayList<>();
        command.add(program.toString());
        command.addAll(Arrays.asList(args));
        // An argument list, no shell; the program is the bundled cmcoder or the user's own full path.
        ProcessBuilder pb = new ProcessBuilder(command).redirectErrorStream(true); // nosemgrep
        if (project != null) pb.directory(project.toFile());
        pb.environment().put("NO_COLOR", "1");
        try {
            Process p = pb.start();
            p.getOutputStream().close();
            CompletableFuture<byte[]> out = CompletableFuture.supplyAsync(() -> {
                try {
                    return p.getInputStream().readAllBytes();
                } catch (IOException e) {
                    return new byte[0];
                }
            });
            if (!p.waitFor(30, TimeUnit.SECONDS)) {
                p.destroyForcibly();
                return "(no answer in 30 s)";
            }
            return new String(out.get(5, TimeUnit.SECONDS), StandardCharsets.UTF_8);
        } catch (IOException | ExecutionException | TimeoutException e) {
            return "(could not run it: " + e.getMessage() + ")";
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return "(interrupted)";
        }
    }
}
