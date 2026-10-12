package cmcoder.idea;

import java.nio.file.InvalidPathException;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

import com.intellij.codeInsight.daemon.impl.DaemonCodeAnalyzerEx;
import com.intellij.codeInsight.daemon.impl.HighlightInfo;
import com.intellij.lang.annotation.HighlightSeverity;
import com.intellij.openapi.application.Application;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.application.ModalityState;
import com.intellij.openapi.application.ReadAction;
import com.intellij.openapi.editor.Document;
import com.intellij.openapi.editor.Editor;
import com.intellij.openapi.editor.SelectionModel;
import com.intellij.openapi.fileEditor.FileDocumentManager;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.OpenFileDescriptor;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.vfs.LocalFileSystem;
import com.intellij.openapi.vfs.VirtualFile;

import cmcoder.ide.core.EditorContext;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Json;

/** What the user has open, and the IDE tools (H7, H8). */
final class Workspace {
    static final List<String> IDE_TOOLS = List.of("getDiagnostics", "openFile");
    static final int MAX_TOOL_LINES = 200;
    static final int MAX_CONTEXT_DIAGNOSTICS = 30;

    private Workspace() {}

    /** {@code read} on the UI thread, waiting for it (also while a dialog is open). */
    static <T> T onUi(Supplier<T> read) {
        Application app = ApplicationManager.getApplication();
        if (app.isDispatchThread()) return read.get();
        AtomicReference<T> result = new AtomicReference<>();
        app.invokeAndWait(() -> result.set(read.get()), ModalityState.any());
        return result.get();
    }

    /** A file's path on disk, or null for anything that isn't a local file. */
    static String path(VirtualFile file) {
        if (file == null || !file.isInLocalFileSystem()) return null;
        try {
            return file.toNioPath().toString();
        } catch (UnsupportedOperationException | InvalidPathException e) {
            return null;
        }
    }

    /** The editor context (any thread), or null when no file is open in an editor. */
    static Map<String, Object> editorContext(Project project) {
        Object[] read = onUi(() -> {
            if (project.isDisposed()) return null;
            Editor editor = FileEditorManager.getInstance(project).getSelectedTextEditor();
            if (editor == null) return null;
            Document doc = editor.getDocument();
            String path = path(FileDocumentManager.getInstance().getFile(doc));
            if (path == null) return null;
            Map<String, Object> selection = null;
            SelectionModel sel = editor.getSelectionModel();
            if (sel.hasSelection()) {
                int start = sel.getSelectionStart();
                int end = sel.getSelectionEnd();
                int startLine = doc.getLineNumber(start);
                int endLine = doc.getLineNumber(end);
                int endColumn = end - doc.getLineStartOffset(endLine);
                selection = EditorContext.selection(path, startLine + 1,
                        EditorContext.endLine(startLine, endLine, endColumn), sel.getSelectedText());
            }
            return new Object[] {path, selection, doc};
        });
        if (read == null) return null;
        String path = (String) read[0];
        @SuppressWarnings("unchecked")
        Map<String, Object> selection = (Map<String, Object>) read[1];
        List<Map<String, Object>> diagnostics = new ArrayList<>();
        for (Problem p : problems(project, (Document) read[2])) {
            if (diagnostics.size() >= MAX_CONTEXT_DIAGNOSTICS) break;
            diagnostics.add(EditorContext.diagnostic(path, p.line, p.severity, p.message, null));
        }
        return EditorContext.of(path, selection, diagnostics);
    }

    // -- problems (H7, H8) ------------------------------------------------------------------

    static final class Problem {
        final int line;
        final String severity;
        final String message;

        Problem(int line, String severity, String message) {
            this.line = line;
            this.severity = severity;
            this.message = message;
        }
    }

    /** A document's errors and warnings as the IDE highlights them (files it has checked: the open ones). */
    static List<Problem> problems(Project project, Document doc) {
        if (doc == null) return List.of();
        return ReadAction.compute(() -> {
            List<Problem> out = new ArrayList<>();
            if (project.isDisposed()) return out;
            DaemonCodeAnalyzerEx.processHighlights(doc, project, HighlightSeverity.WARNING, 0, doc.getTextLength(), info -> {
                String severity = severity(info);
                String message = info.getDescription();
                if (severity != null && message != null && !message.isBlank()) {
                    out.add(new Problem(doc.getLineNumber(Math.min(info.getStartOffset(), doc.getTextLength())) + 1, severity, message));
                }
                return true;
            });
            return out;
        });
    }

    private static String severity(HighlightInfo info) {
        HighlightSeverity s = info.getSeverity();
        if (s.compareTo(HighlightSeverity.ERROR) >= 0) return "error";
        if (s.compareTo(HighlightSeverity.WARNING) >= 0) return "warning";
        return null;
    }

    // -- IDE tools (H8) -------------------------------------------------------------------------

    static void runIdeTool(Project project, String name, Map<String, Object> input, Path projectDir, Ide.ToolDone done) {
        String file = Json.string(input, "file_path");
        switch (name) {
            case "getDiagnostics":
                ApplicationManager.getApplication().executeOnPooledThread(() -> {
                    try {
                        done.done(diagnostics(project, file, projectDir), false);
                    } catch (RuntimeException e) {
                        done.done("Could not read the problems: " + e.getMessage(), true);
                    }
                });
                return;
            case "openFile":
                if (file == null) {
                    done.done("file_path is required.", true);
                    return;
                }
                long line = Json.number(input, "line", -1);
                ApplicationManager.getApplication().invokeLater(() -> openFile(project, resolve(file, projectDir), line, done));
                return;
            default:
                done.done("Unknown IDE tool " + name + ".", true);
        }
    }

    static Path resolve(String file, Path projectDir) {
        Path p = Path.of(file);
        return p.isAbsolute() || projectDir == null ? p : projectDir.resolve(p);
    }

    /**
     * Problems of a file, or of the files open in editors when none is named
     * (the IDE checks the open files): "path:line:1 severity: message".
     */
    static String diagnostics(Project project, String file, Path projectDir) {
        List<VirtualFile> files = new ArrayList<>();
        if (file != null) {
            VirtualFile vf = LocalFileSystem.getInstance().findFileByNioFile(resolve(file, projectDir));
            if (vf == null) return "No such file: " + file;
            files.add(vf);
        } else {
            VirtualFile[] open = onUi(() -> project.isDisposed() ? new VirtualFile[0] : FileEditorManager.getInstance(project).getOpenFiles());
            if (open != null) files.addAll(List.of(open));
        }
        List<String> lines = new ArrayList<>();
        int total = 0;
        for (VirtualFile vf : files) {
            String path = path(vf);
            if (path == null) continue;
            Path p = Path.of(path);
            String shown = projectDir != null && p.startsWith(projectDir) ? projectDir.relativize(p).toString().replace('\\', '/') : path;
            Document doc = ReadAction.compute(() -> FileDocumentManager.getInstance().getDocument(vf));
            for (Problem problem : problems(project, doc)) {
                total++;
                if (lines.size() >= MAX_TOOL_LINES) continue;
                lines.add(shown + ":" + problem.line + ":1 " + problem.severity + ": " + problem.message);
            }
        }
        if (lines.isEmpty()) {
            return file != null ? "No problems in this file (the IDE checks files that are open in an editor)."
                    : "No problems in the open files.";
        }
        String more = total > lines.size() ? "\n… " + (total - lines.size()) + " more" : "";
        return String.join("\n", lines) + more;
    }

    /** UI thread. */
    private static void openFile(Project project, Path file, long line, Ide.ToolDone done) {
        try {
            if (project.isDisposed()) {
                done.done("The project is closed.", true);
                return;
            }
            VirtualFile vf = LocalFileSystem.getInstance().refreshAndFindFileByNioFile(file);
            if (vf == null || vf.isDirectory()) {
                done.done("No such file: " + file, true);
                return;
            }
            if (line > 0) {
                new OpenFileDescriptor(project, vf, (int) Math.min(line - 1, Integer.MAX_VALUE), 0).navigate(true);
            } else {
                FileEditorManager.getInstance(project).openFile(vf, true);
            }
            done.done("Opened " + vf.getName() + " in the editor.", false);
        } catch (RuntimeException e) {
            done.done("Could not open " + file + ": " + e.getMessage(), true);
        }
    }
}
