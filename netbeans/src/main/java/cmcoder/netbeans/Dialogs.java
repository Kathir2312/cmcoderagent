package cmcoder.netbeans;

import java.awt.BorderLayout;
import java.io.IOException;
import java.net.URL;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.function.Consumer;
import java.util.stream.Stream;

import javax.swing.DefaultListModel;
import javax.swing.JLabel;
import javax.swing.JList;
import javax.swing.JPanel;
import javax.swing.JScrollPane;
import javax.swing.JTextField;
import javax.swing.ListSelectionModel;
import javax.swing.event.DocumentEvent;
import javax.swing.event.DocumentListener;

import org.openide.DialogDescriptor;
import org.openide.DialogDisplayer;
import org.openide.NotifyDescriptor;
import org.openide.awt.HtmlBrowser;

import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/** The pickers the chat asks for (H11, H13, H18). Swing thread only. */
final class Dialogs {
    private static final int MAX_FILES = 20000;

    private Dialogs() {}

    /** One of {@code options}, or -1 if cancelled. */
    static int choose(String title, String message, int type, String... options) {
        NotifyDescriptor d = new NotifyDescriptor(message, title, NotifyDescriptor.DEFAULT_OPTION, type, options, options[0]);
        Object answer = DialogDisplayer.getDefault().notify(d);
        for (int i = 0; i < options.length; i++) if (options[i].equals(answer)) return i;
        return -1;
    }

    static void info(String message) {
        DialogDisplayer.getDefault().notify(new NotifyDescriptor.Message(message, NotifyDescriptor.INFORMATION_MESSAGE));
    }

    static void warn(String message) {
        DialogDisplayer.getDefault().notify(new NotifyDescriptor.Message(message, NotifyDescriptor.WARNING_MESSAGE));
    }

    static void error(String message) {
        DialogDisplayer.getDefault().notify(new NotifyDescriptor.Message(message, NotifyDescriptor.ERROR_MESSAGE));
    }

    static boolean confirm(String message) {
        NotifyDescriptor d = new NotifyDescriptor.Confirmation(message, Brand.product(), NotifyDescriptor.OK_CANCEL_OPTION);
        return DialogDisplayer.getDefault().notify(d) == NotifyDescriptor.OK_OPTION;
    }

    /** One item of a list (filtered as you type), or null if cancelled. */
    static <T> T pick(String title, String message, List<T> items, java.util.function.Function<T, String> label) {
        DefaultListModel<String> model = new DefaultListModel<>();
        List<T> shown = new ArrayList<>(items);
        JList<String> list = new JList<>(model);
        list.setSelectionMode(ListSelectionModel.SINGLE_SELECTION);
        JTextField filter = new JTextField();
        Runnable refill = () -> {
            String f = filter.getText().toLowerCase(java.util.Locale.ROOT);
            model.clear();
            shown.clear();
            for (T item : items) {
                String text = label.apply(item);
                if (f.isEmpty() || text.toLowerCase(java.util.Locale.ROOT).contains(f)) {
                    shown.add(item);
                    model.addElement(text);
                }
                if (model.size() >= 500) break;
            }
            if (!model.isEmpty()) list.setSelectedIndex(0);
        };
        filter.getDocument().addDocumentListener(new DocumentListener() {
            @Override
            public void insertUpdate(DocumentEvent e) {
                refill.run();
            }

            @Override
            public void removeUpdate(DocumentEvent e) {
                refill.run();
            }

            @Override
            public void changedUpdate(DocumentEvent e) {
                refill.run();
            }
        });
        refill.run();
        JPanel panel = new JPanel(new BorderLayout(4, 4));
        JPanel top = new JPanel(new BorderLayout(4, 4));
        top.add(new JLabel(message), BorderLayout.NORTH);
        top.add(filter, BorderLayout.SOUTH);
        panel.add(top, BorderLayout.NORTH);
        panel.add(new JScrollPane(list), BorderLayout.CENTER);
        panel.setPreferredSize(new java.awt.Dimension(560, 360));
        DialogDescriptor d = new DialogDescriptor(panel, title);
        if (DialogDisplayer.getDefault().notify(d) != DialogDescriptor.OK_OPTION) return null;
        int i = list.getSelectedIndex();
        return i < 0 || i >= shown.size() ? null : shown.get(i);
    }

    /** A line of text, or null if cancelled; {@code secret} hides what's typed. */
    static String ask(String title, String prompt, boolean secret) {
        NotifyDescriptor.InputLine d = secret ? new NotifyDescriptor.PasswordLine(prompt, title) : new NotifyDescriptor.InputLine(prompt, title);
        return DialogDisplayer.getDefault().notify(d) == NotifyDescriptor.OK_OPTION ? d.getInputText() : null;
    }

    /** /rewind: which message, then what goes back (and files outside the project?). */
    static void pickRewind(List<Protocol.RewindPoint> points, Consumer<Ide.RewindChoice> chosen) {
        List<Protocol.RewindPoint> newestFirst = new ArrayList<>(points);
        Collections.reverse(newestFirst);
        Protocol.RewindPoint point = pick(Brand.product() + ": Rewind", "Rewind to before which message?", newestFirst, p -> {
            String text = p.text == null || p.text.isEmpty() ? "(empty message)" : p.text.replaceAll("\\s+", " ");
            return text + (p.filesChanged > 0 ? "  —  " + p.filesChanged + " file(s) changed since" : "");
        });
        if (point == null) return;
        int answer = choose(Brand.product() + ": Rewind", "What should go back? (Changes made by Bash commands are not undone.)",
                NotifyDescriptor.QUESTION_MESSAGE, "Code and conversation", "Conversation only", "Code only", "Cancel");
        if (answer < 0 || answer > 2) return;
        boolean code = answer != 1;
        boolean conversation = answer != 2;
        boolean outside = false;
        if (code && !point.outsideFiles.isEmpty()) {
            List<String> shown = point.outsideFiles.subList(0, Math.min(10, point.outsideFiles.size()));
            int a = choose(Brand.product() + ": Rewind", point.outsideFiles.size()
                    + " changed file(s) are outside the project. Restore them too?\n\n" + String.join("\n", shown),
                    NotifyDescriptor.WARNING_MESSAGE, "Restore them too", "Only the project's files", "Cancel");
            if (a < 0 || a > 1) return;
            outside = a == 0;
        }
        chosen.accept(new Ide.RewindChoice(point.turn, code, conversation, outside));
    }

    /** The @ button: a file of the project, as a path relative to it. */
    static void attachFile(Path projectDir, Consumer<String> chosen) {
        if (projectDir == null) {
            warn("Open a project first.");
            return;
        }
        List<String> files = new ArrayList<>();
        try (Stream<Path> all = Files.walk(projectDir)) {
            all.filter(p -> Files.isRegularFile(p) && !hidden(projectDir.relativize(p)))
                    .limit(MAX_FILES)
                    .forEach(p -> files.add(projectDir.relativize(p).toString().replace('\\', '/')));
        } catch (IOException | RuntimeException e) {
            Plugin.error("Listing the project's files failed", e);
        }
        Collections.sort(files);
        String file = pick(Brand.product() + ": Attach a file to the message (@)", "Type part of a file's name:", files, s -> s);
        if (file != null) chosen.accept(file);
    }

    private static boolean hidden(Path relative) {
        for (Path part : relative) {
            String s = part.toString();
            if (s.startsWith(".") || s.equals("node_modules") || s.equals("target") || s.equals("build")) return true;
        }
        return false;
    }

    /** An http(s) address in the system browser (Host has checked it). */
    static void openExternal(String url) {
        try {
            HtmlBrowser.URLDisplayer.getDefault().showURLExternal(new URL(url));
        } catch (Exception e) {
            Plugin.error("Could not open " + url, e);
        }
    }
}
