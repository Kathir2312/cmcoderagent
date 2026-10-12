package cmcoder.idea;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.function.Consumer;

import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.application.ReadAction;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.roots.ProjectFileIndex;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.ui.popup.JBPopupFactory;
import com.intellij.ui.SimpleListCellRenderer;

import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Protocol;

/** The rewind pickers and the attach-file picker (H11, H13). UI thread unless said otherwise. */
final class Dialogs {
    static final int MAX_FILES = 5000;

    private Dialogs() {}

    /** /rewind: which message, then what goes back, then (if any) files outside the project. */
    static void pickRewind(Project project, List<Protocol.RewindPoint> points, Consumer<Ide.RewindChoice> chosen) {
        if (project.isDisposed() || points.isEmpty()) return;
        List<Protocol.RewindPoint> newestFirst = new ArrayList<>(points);
        Collections.reverse(newestFirst);
        JBPopupFactory.getInstance()
                .createPopupChooserBuilder(newestFirst)
                .setTitle(Brand.product() + ": rewind to before which message?")
                .setRenderer(SimpleListCellRenderer.<Protocol.RewindPoint>create("", Dialogs::label))
                .setNamerForFiltering(Dialogs::label)
                .setItemChosenCallback(point -> afterPoint(project, point, chosen))
                .createPopup()
                .showCenteredInCurrentWindow(project);
    }

    static String label(Protocol.RewindPoint p) {
        String text = p.text == null || p.text.isEmpty() ? "(empty message)" : p.text.replaceAll("\\s+", " ");
        if (text.length() > 120) text = text.substring(0, 117) + "…";
        return text + (p.filesChanged > 0 ? "  —  " + p.filesChanged + " file(s) changed since" : "");
    }

    private static void afterPoint(Project project, Protocol.RewindPoint point, Consumer<Ide.RewindChoice> chosen) {
        String title = Brand.product() + ": Rewind";
        int answer = Messages.showDialog(project, "What should go back? (Changes made by Bash commands are not undone.)", title,
                new String[] {"Code and conversation", "Conversation only", "Code only", "Cancel"}, 0, Messages.getQuestionIcon());
        if (answer < 0 || answer > 2) return;
        boolean code = answer != 1;
        boolean conversation = answer != 2;
        boolean outside = false;
        if (code && !point.outsideFiles.isEmpty()) {
            List<String> shown = point.outsideFiles.subList(0, Math.min(10, point.outsideFiles.size()));
            int a = Messages.showDialog(project, point.outsideFiles.size()
                    + " changed file(s) are outside the project. Restore them too?\n\n" + String.join("\n", shown), title,
                    new String[] {"Restore them too", "Only the project's files", "Cancel"}, 1, Messages.getWarningIcon());
            if (a < 0 || a > 1) return;
            outside = a == 0;
        }
        chosen.accept(new Ide.RewindChoice(point.turn, code, conversation, outside));
    }

    /** The @ button: a file of the project (as the IDE indexes it, without excluded folders), relative to it. */
    static void attachFile(Project project, Path projectDir, Consumer<String> chosen) {
        if (project.isDisposed()) return;
        if (projectDir == null) {
            Messages.showWarningDialog(project, "Open a project first.", Brand.product());
            return;
        }
        // Listing a big project takes a moment: not on the UI thread.
        ApplicationManager.getApplication().executeOnPooledThread(() -> {
            List<String> files = ReadAction.compute(() -> {
                List<String> out = new ArrayList<>();
                if (project.isDisposed()) return out;
                ProjectFileIndex.getInstance(project).iterateContent(vf -> {
                    if (!vf.isDirectory()) {
                        String path = Workspace.path(vf);
                        if (path != null) {
                            Path p = Path.of(path);
                            if (p.startsWith(projectDir)) out.add(projectDir.relativize(p).toString().replace('\\', '/'));
                        }
                    }
                    return out.size() < MAX_FILES;
                });
                return out;
            });
            Collections.sort(files);
            ApplicationManager.getApplication().invokeLater(() -> {
                if (project.isDisposed()) return;
                JBPopupFactory.getInstance()
                        .createPopupChooserBuilder(files)
                        .setTitle(Brand.product() + ": attach a file to the message (@), type to filter")
                        .setNamerForFiltering(s -> s)
                        .setItemChosenCallback(chosen::accept)
                        .createPopup()
                        .showCenteredInCurrentWindow(project);
            });
        });
    }
}
