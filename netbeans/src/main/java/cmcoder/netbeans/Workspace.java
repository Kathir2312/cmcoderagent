package cmcoder.netbeans;

import java.io.File;
import java.lang.reflect.InvocationTargetException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

import javax.swing.JEditorPane;
import javax.swing.SwingUtilities;
import javax.swing.text.Document;
import javax.swing.text.Element;
import javax.swing.text.JTextComponent;
import javax.swing.text.StyledDocument;

import org.netbeans.api.editor.EditorRegistry;
import org.netbeans.api.editor.mimelookup.MimeLookup;
import org.netbeans.api.lsp.Diagnostic;
import org.netbeans.api.project.FileOwnerQuery;
import org.netbeans.api.project.Project;
import org.netbeans.api.project.ui.OpenProjects;
import org.netbeans.spi.lsp.ErrorProvider;
import org.openide.cookies.EditorCookie;
import org.openide.cookies.LineCookie;
import org.openide.filesystems.FileObject;
import org.openide.filesystems.FileUtil;
import org.openide.loaders.DataObject;
import org.openide.text.Line;
import org.openide.text.NbDocument;
import org.openide.windows.Mode;
import org.openide.windows.TopComponent;
import org.openide.windows.WindowManager;

import cmcoder.ide.core.EditorContext;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Json;

/** What the user has open, the project cmcoder works on, and the IDE tools (H7, H8). */
final class Workspace {
    static final List<String> IDE_TOOLS = Collections.unmodifiableList(Arrays.asList("getDiagnostics", "openFile"));
    static final int MAX_TOOL_LINES = 200;
    static final int MAX_CONTEXT_DIAGNOSTICS = 30;

    private Workspace() {}

    /** {@code read} on the Swing thread, waiting for it. */
    static <T> T onUi(Supplier<T> read) {
        if (SwingUtilities.isEventDispatchThread()) return read.get();
        AtomicReference<T> result = new AtomicReference<>();
        try {
            SwingUtilities.invokeAndWait(() -> result.set(read.get()));
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            return null;
        } catch (InvocationTargetException e) {
            Plugin.error("Reading the editor failed", e.getCause());
            return null;
        }
        return result.get();
    }

    /**
     * The editor the user last worked in, if it shows a file; before any editor
     * had the keyboard, the one in the editor tab that's in front.
     */
    static JTextComponent editor() {
        JTextComponent c = EditorRegistry.lastFocusedComponent();
        if (c != null && file(c.getDocument()) != null) return c;
        Mode mode = WindowManager.getDefault().findMode("editor");
        TopComponent tab = mode == null ? null : mode.getSelectedTopComponent();
        EditorCookie cookie = tab == null ? null : tab.getLookup().lookup(EditorCookie.class);
        JEditorPane[] panes = cookie == null ? null : cookie.getOpenedPanes();
        c = panes == null || panes.length == 0 ? null : panes[0];
        return c != null && file(c.getDocument()) != null ? c : null;
    }

    static FileObject file(Document doc) {
        if (doc == null) return null;
        Object source = doc.getProperty(Document.StreamDescriptionProperty);
        if (source instanceof DataObject) return ((DataObject) source).getPrimaryFile();
        if (source instanceof FileObject) return (FileObject) source;
        return null;
    }

    /**
     * The project folder (Swing thread): the active editor's project, else the
     * main project, else the only open project; for a file outside any project,
     * its git repository. Null otherwise.
     */
    static Path projectDir() {
        JTextComponent editor = editor();
        FileObject file = editor == null ? null : file(editor.getDocument());
        Project project = file == null ? null : FileOwnerQuery.getOwner(file);
        if (project == null) project = OpenProjects.getDefault().getMainProject();
        if (project == null) {
            Project[] open = OpenProjects.getDefault().getOpenProjects();
            if (open.length == 1) project = open[0];
        }
        File folder = project == null ? null : FileUtil.toFile(project.getProjectDirectory());
        if (folder != null) return folder.toPath();
        File f = file == null ? null : FileUtil.toFile(file);
        for (Path p = f == null ? null : f.toPath().getParent(); p != null; p = p.getParent()) {
            if (Files.exists(p.resolve(".git"))) return p;
        }
        return null;
    }

    /** The editor context (any thread: the editor is read on the Swing thread), or null when no file is open. */
    static Map<String, Object> editorContext() {
        Object[] read = onUi(() -> {
            JTextComponent editor = editor();
            if (editor == null) return null;
            FileObject fo = file(editor.getDocument());
            File f = fo == null ? null : FileUtil.toFile(fo);
            if (f == null) return null;
            String path = f.getPath();
            Map<String, Object> selection = null;
            int start = editor.getSelectionStart();
            int end = editor.getSelectionEnd();
            if (end > start) {
                Element root = editor.getDocument().getDefaultRootElement();
                int startLine = root.getElementIndex(start);
                int endLine = root.getElementIndex(end);
                int endColumn = end - root.getElement(endLine).getStartOffset();
                selection = EditorContext.selection(path, startLine + 1, EditorContext.endLine(startLine, endLine, endColumn),
                        editor.getSelectedText());
            }
            return new Object[] {path, selection, fo};
        });
        if (read == null) return null;
        String path = (String) read[0];
        @SuppressWarnings("unchecked")
        Map<String, Object> selection = (Map<String, Object>) read[1];
        List<Map<String, Object>> diagnostics = new ArrayList<>();
        for (Problem p : problems((FileObject) read[2])) {
            if (diagnostics.size() >= MAX_CONTEXT_DIAGNOSTICS) break;
            diagnostics.add(EditorContext.diagnostic(path, p.line, p.severity, p.message, p.source));
        }
        return EditorContext.of(path, selection, diagnostics);
    }

    // -- problems (H7, H8) ------------------------------------------------------------------

    static final class Problem {
        final int line;
        final String severity;
        final String message;
        final String source;

        Problem(int line, String severity, String message, String source) {
            this.line = line;
            this.severity = severity;
            this.message = message;
            this.source = source;
        }
    }

    /**
     * A file's errors and warnings as NetBeans shows them, from the error
     * providers registered for its type (Java and others that have one; the
     * same ones NetBeans' language server uses). Not on the Swing thread: they
     * may parse the file.
     */
    static List<Problem> problems(FileObject file) {
        List<Problem> out = new ArrayList<>();
        if (file == null || !file.isValid()) return out;
        String text = null;
        for (ErrorProvider provider : MimeLookup.getLookup(file.getMIMEType()).lookupAll(ErrorProvider.class)) {
            List<? extends Diagnostic> found;
            try {
                found = provider.computeErrors(new ErrorProvider.Context(file, ErrorProvider.Kind.ERRORS));
            } catch (RuntimeException e) {
                Plugin.error("Problems of " + file.getPath() + " failed", e);
                continue;
            }
            if (found == null) continue;
            for (Diagnostic d : found) {
                if (text == null) text = text(file);
                int offset = d.getStartPosition() == null ? 0 : d.getStartPosition().getOffset();
                out.add(new Problem(lineOf(text, offset), severity(d.getSeverity()), d.getDescription(), d.getCode()));
            }
        }
        return out;
    }

    private static String text(FileObject file) {
        try {
            return file.asText();
        } catch (java.io.IOException e) {
            return "";
        }
    }

    static int lineOf(String text, int offset) {
        int line = 1;
        for (int i = 0; i < Math.min(offset, text.length()); i++) if (text.charAt(i) == '\n') line++;
        return line;
    }

    static String severity(Diagnostic.Severity s) {
        if (s == null) return "info";
        switch (s) {
            case Error:
                return "error";
            case Warning:
                return "warning";
            default:
                return "info";
        }
    }

    // -- IDE tools (H8) -------------------------------------------------------------------------

    static void runIdeTool(String name, Map<String, Object> input, Path projectDir, Ide.ToolDone done) {
        String file = Json.string(input, "file_path");
        switch (name) {
            case "getDiagnostics":
                done.done(diagnostics(file, projectDir), false);
                return;
            case "openFile":
                if (file == null) {
                    done.done("file_path is required.", true);
                    return;
                }
                long line = Json.number(input, "line", -1);
                SwingUtilities.invokeLater(() -> openFile(resolve(file, projectDir), line, done));
                return;
            default:
                done.done("Unknown IDE tool " + name + ".", true);
        }
    }

    private static File resolve(String file, Path projectDir) {
        File f = new File(file);
        return f.isAbsolute() || projectDir == null ? f : projectDir.resolve(file).toFile();
    }

    /**
     * Problems of a file, or of the files open in editors when none is named
     * (NetBeans keeps no list of every file's problems): "path:line:1 severity:
     * message (source)".
     */
    static String diagnostics(String file, Path projectDir) {
        List<FileObject> files = new ArrayList<>();
        if (file != null) {
            FileObject fo = FileUtil.toFileObject(FileUtil.normalizeFile(resolve(file, projectDir)));
            if (fo == null) return "No problems in this file.";
            files.add(fo);
        } else {
            List<FileObject> open = onUi(() -> {
                List<FileObject> all = new ArrayList<>();
                for (JTextComponent c : EditorRegistry.componentList()) {
                    FileObject fo = file(c.getDocument());
                    if (fo != null && !all.contains(fo)) all.add(fo);
                }
                return all;
            });
            if (open != null) files.addAll(open);
        }
        List<String> lines = new ArrayList<>();
        int total = 0;
        for (FileObject fo : files) {
            File f = FileUtil.toFile(fo);
            String shown = f == null ? fo.getPath()
                    : projectDir != null && f.toPath().startsWith(projectDir) ? projectDir.relativize(f.toPath()).toString().replace('\\', '/') : f.getPath();
            for (Problem p : problems(fo)) {
                total++;
                if (lines.size() >= MAX_TOOL_LINES) continue;
                lines.add(shown + ":" + p.line + ":1 " + p.severity + ": " + p.message + (p.source != null ? " (" + p.source + ")" : ""));
            }
        }
        if (lines.isEmpty()) return file != null ? "No problems in this file." : "No problems in the open files.";
        String more = total > lines.size() ? "\n… " + (total - lines.size()) + " more" : "";
        return String.join("\n", lines) + more;
    }

    /** Swing thread. */
    private static void openFile(File file, long line, Ide.ToolDone done) {
        try {
            FileObject fo = FileUtil.toFileObject(FileUtil.normalizeFile(file));
            if (fo == null) {
                done.done("No such file: " + file, true);
                return;
            }
            DataObject data = DataObject.find(fo);
            LineCookie lines = data.getLookup().lookup(LineCookie.class);
            EditorCookie editor = data.getLookup().lookup(EditorCookie.class);
            if (lines != null && line > 0) {
                try {
                    if (editor != null) {
                        StyledDocument doc = editor.openDocument();
                        int count = NbDocument.findLineRootElement(doc).getElementCount();
                        Line l = lines.getLineSet().getOriginal((int) Math.min(line - 1, Math.max(0, count - 1)));
                        l.show(Line.ShowOpenType.OPEN, Line.ShowVisibilityType.FOCUS);
                    }
                } catch (IndexOutOfBoundsException e) {
                    if (editor != null) editor.open();
                }
            } else if (editor != null) {
                editor.open();
            } else {
                done.done("Can't open " + file.getName() + " in an editor.", true);
                return;
            }
            done.done("Opened " + file.getName() + " in the editor.", false);
        } catch (java.io.IOException | RuntimeException e) {
            done.done("Could not open " + file + ": " + e.getMessage(), true);
        }
    }
}
