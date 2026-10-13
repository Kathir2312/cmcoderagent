package cmcoder.netbeans;

import java.awt.Toolkit;
import java.awt.datatransfer.StringSelection;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;

import javax.swing.SwingUtilities;

import org.openide.awt.ActionID;
import org.openide.awt.ActionReference;
import org.openide.awt.ActionReferences;
import org.openide.awt.ActionRegistration;
import org.openide.modules.Places;
import org.openide.util.RequestProcessor;

import cmcoder.ide.core.ProgramLocator;

/** The module's actions (Tools → cmcoder, the editor's menu, shortcuts). */
public final class Actions {
    private Actions() {}

    /** Ask about the selection (H14): opens the chat, then the context and a prompt. */
    @ActionID(category = "Tools", id = "cmcoder.netbeans.AskAboutSelection")
    @ActionRegistration(displayName = "Ask cmcoder About Selection")
    @ActionReferences({
        @ActionReference(path = "Menu/Tools/cmcoder", position = 200),
        @ActionReference(path = "Editors/Popup", position = 4100),
        @ActionReference(path = "Shortcuts", name = "DA-K"),
    })
    public static final class AskAboutSelection implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            ChatWindow.showIt();
            Session.get().run(h -> h.askAboutSelection());
        }
    }

    @ActionID(category = "Tools", id = "cmcoder.netbeans.NewConversation")
    @ActionRegistration(displayName = "New cmcoder Conversation")
    @ActionReference(path = "Menu/Tools/cmcoder", position = 300)
    public static final class NewConversation implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            ChatWindow.showIt();
            Session.get().run(h -> h.newConversation(Collections.emptyList()));
        }
    }

    @ActionID(category = "Tools", id = "cmcoder.netbeans.Stop")
    @ActionRegistration(displayName = "Stop cmcoder")
    @ActionReference(path = "Menu/Tools/cmcoder", position = 400)
    public static final class Stop implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            Session.get().run(h -> h.interrupt());
        }
    }

    @ActionID(category = "Tools", id = "cmcoder.netbeans.OpenNavigator")
    @ActionRegistration(displayName = "Open cmcoder Agent Navigator")
    @ActionReference(path = "Menu/Tools/cmcoder", position = 500)
    public static final class OpenNavigator implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            NavigatorWindow.showIt();
        }
    }

    @ActionID(category = "Tools", id = "cmcoder.netbeans.ShowLog")
    @ActionRegistration(displayName = "Show cmcoder Log")
    @ActionReference(path = "Menu/Tools/cmcoder", position = 600)
    public static final class ShowLog implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            Plugin.showLog();
        }
    }

    /**
     * Copy Diagnostics (H22): what support needs, to the clipboard; nothing is
     * sent anywhere, and cmcoder masks keys in what it prints. Runs cmcoder
     * (version, doctor) in the background, so the UI doesn't wait.
     */
    @ActionID(category = "Tools", id = "cmcoder.netbeans.CopyDiagnostics")
    @ActionRegistration(displayName = "Copy cmcoder Diagnostics")
    @ActionReference(path = "Menu/Tools/cmcoder", position = 700)
    public static final class CopyDiagnostics implements ActionListener {
        @Override
        public void actionPerformed(ActionEvent e) {
            Path project = Workspace.projectDir();
            RequestProcessor.getDefault().post(() -> {
                String text = diagnostics(project, true);
                SwingUtilities.invokeLater(() -> {
                    Toolkit.getDefaultToolkit().getSystemClipboard().setContents(new StringSelection(text), null);
                    Dialogs.info("The diagnostics are on the clipboard. Read them before you share them.");
                });
            });
        }
    }

    static String diagnostics(Path project, boolean runProgram) {
        StringBuilder s = new StringBuilder();
        s.append(Brand.product()).append(" for NetBeans ").append(Plugin.version()).append('\n');
        s.append("NetBeans: ").append(System.getProperty("netbeans.buildnumber", "?")).append('\n');
        s.append("User folder: ").append(Places.getUserDirectory()).append('\n');
        s.append("OS: ").append(System.getProperty("os.name")).append(' ').append(System.getProperty("os.version"))
                .append(' ').append(System.getProperty("os.arch")).append('\n');
        s.append("Java: ").append(System.getProperty("java.version")).append(' ').append(System.getProperty("java.vendor")).append('\n');
        s.append("JavaFX: ").append(System.getProperty("javafx.runtime.version", "not started")).append('\n');
        ProgramLocator.Found found = ProgramLocator.find(Options.program(), Plugin.pluginDir(), project, new HashMap<>(System.getenv()));
        s.append("Program: ").append(found.program != null ? found.program : "not found: " + found.problem).append('\n');
        if (runProgram && found.program != null) {
            s.append("Version: ").append(output(found.program, project, "--version").trim()).append('\n');
            s.append("\ncmcoder doctor --no-probe:\n").append(output(found.program, project, "doctor", "--no-probe")).append('\n');
        }
        s.append("State: ").append(Session.get().state()).append('\n');
        s.append("Last state message: ").append(Session.get().lastState()).append('\n');
        s.append("\nRecent log:\n");
        for (String line : Plugin.recentLog()) s.append(line).append('\n');
        return s.toString();
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
