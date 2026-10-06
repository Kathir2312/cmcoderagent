package cmcoder.eclipse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import org.eclipse.core.commands.AbstractHandler;
import org.eclipse.core.commands.ExecutionEvent;
import org.eclipse.core.runtime.jobs.Job;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.swt.dnd.Clipboard;
import org.eclipse.swt.dnd.TextTransfer;
import org.eclipse.swt.dnd.Transfer;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.console.ConsolePlugin;

import cmcoder.ide.core.ProgramLocator;

/** The plugin's commands (menus, keys, Quick Access). */
public final class Commands {
    private Commands() {}

    public static final class Open extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Session.showView(Session.CHAT_VIEW, IWorkbenchPage.VIEW_ACTIVATE);
            return null;
        }
    }

    /** Ask about the selection (H14): opens the chat, then the context and a prompt. */
    public static final class AskAboutSelection extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Session.showView(Session.CHAT_VIEW, IWorkbenchPage.VIEW_VISIBLE);
            Session.get().run(h -> h.askAboutSelection());
            return null;
        }
    }

    public static final class NewConversation extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Session.showView(Session.CHAT_VIEW, IWorkbenchPage.VIEW_VISIBLE);
            Session.get().run(h -> h.newConversation(java.util.Collections.emptyList()));
            return null;
        }
    }

    public static final class Stop extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Session.get().run(h -> h.interrupt());
            return null;
        }
    }

    public static final class OpenNavigator extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Session.showView(Session.NAVIGATOR_VIEW, IWorkbenchPage.VIEW_ACTIVATE);
            return null;
        }
    }

    public static final class ShowLog extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            ConsolePlugin.getDefault().getConsoleManager().showConsoleView(Activator.get().console());
            return null;
        }
    }

    /**
     * Copy Diagnostics (H22): what support needs, to the clipboard; nothing is
     * sent anywhere, and cmcoder masks keys in what it prints. Runs cmcoder
     * (version, doctor) in the background, so the UI doesn't wait.
     */
    public static final class CopyDiagnostics extends AbstractHandler {
        @Override
        public Object execute(ExecutionEvent event) {
            Path project = Workspace.projectDir();
            Job job = Job.create(Brand.product() + ": collecting diagnostics", monitor -> {
                String text = diagnostics(project, true);
                Display display = PlatformUI.getWorkbench().getDisplay();
                if (display.isDisposed()) return;
                display.asyncExec(() -> {
                    Clipboard clipboard = new Clipboard(display);
                    try {
                        clipboard.setContents(new Object[] {text}, new Transfer[] {TextTransfer.getInstance()});
                    } finally {
                        clipboard.dispose();
                    }
                    MessageDialog.openInformation(PlatformUI.getWorkbench().getModalDialogShellProvider().getShell(),
                            Brand.product(), "The diagnostics are on the clipboard. Read them before you share them.");
                });
            });
            job.schedule();
            return null;
        }
    }

    /** Without running cmcoder (tests, quick checks). */
    static String diagnostics() {
        return diagnostics(Workspace.onUi(Workspace::projectDir), false);
    }

    static String diagnostics(Path project, boolean runProgram) {
        StringBuilder s = new StringBuilder();
        s.append(Brand.product()).append(" for Eclipse ").append(Activator.get().version()).append('\n');
        s.append("Eclipse: ").append(System.getProperty("eclipse.buildId", "?")).append('\n');
        s.append("OS: ").append(System.getProperty("os.name")).append(' ').append(System.getProperty("os.version"))
                .append(' ').append(System.getProperty("os.arch")).append('\n');
        s.append("Java: ").append(System.getProperty("java.version")).append('\n');
        String setting = Preferences.store().getString(Preferences.PROGRAM);
        Map<String, String> env = new HashMap<>(System.getenv());
        ProgramLocator.Found found = ProgramLocator.find(setting == null || setting.isBlank() ? null : setting.trim(),
                Activator.pluginDir(), project, env);
        s.append("Program: ").append(found.program != null ? found.program : "not found: " + found.problem).append('\n');
        if (runProgram && found.program != null) {
            s.append("Version: ").append(output(found.program, project, "--version").trim()).append('\n');
            s.append("\ncmcoder doctor --no-probe:\n").append(output(found.program, project, "doctor", "--no-probe")).append('\n');
        }
        s.append("State: ").append(Session.get().state()).append('\n');
        s.append("Last state message: ").append(Session.get().lastState()).append('\n');
        s.append("\nRecent log:\n");
        for (String line : Activator.get().recentLog()) s.append(line).append('\n');
        return s.toString();
    }

    /** What the program prints (stdout and stderr), at most 30 s; no shell. */
    private static String output(Path program, Path project, String... args) {
        List<String> command = new ArrayList<>();
        command.add(program.toString());
        command.addAll(Arrays.asList(args));
        ProcessBuilder pb = new ProcessBuilder(command).redirectErrorStream(true);
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
