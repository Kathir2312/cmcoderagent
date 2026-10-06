package cmcoder.eclipse;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Supplier;

import org.eclipse.core.resources.IFile;
import org.eclipse.core.resources.IMarker;
import org.eclipse.core.resources.IProject;
import org.eclipse.core.resources.IResource;
import org.eclipse.core.resources.IWorkspaceRoot;
import org.eclipse.core.resources.ResourcesPlugin;
import org.eclipse.core.runtime.CoreException;
import org.eclipse.core.runtime.IAdaptable;
import org.eclipse.core.runtime.IPath;
import org.eclipse.jface.text.BadLocationException;
import org.eclipse.jface.text.IDocument;
import org.eclipse.jface.text.ITextSelection;
import org.eclipse.jface.viewers.ISelection;
import org.eclipse.jface.viewers.IStructuredSelection;
import org.eclipse.swt.widgets.Display;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IFileEditorInput;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.PartInitException;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.ide.IDE;
import org.eclipse.ui.texteditor.ITextEditor;

import cmcoder.ide.core.EditorContext;
import cmcoder.ide.core.Ide;
import cmcoder.ide.core.Json;

/** What the user has open, the project cmcoder works on, and the IDE tools (H7, H8). */
final class Workspace {
    static final List<String> IDE_TOOLS = Collections.unmodifiableList(Arrays.asList("getDiagnostics", "openFile"));
    static final int MAX_TOOL_LINES = 200;

    private Workspace() {}

    /** {@code read} on the UI thread, waiting for it; null if the workbench is gone. */
    static <T> T onUi(Supplier<T> read) {
        if (!PlatformUI.isWorkbenchRunning()) return null;
        Display display = PlatformUI.getWorkbench().getDisplay();
        if (display.isDisposed()) return null;
        if (display.getThread() == Thread.currentThread()) return read.get();
        AtomicReference<T> result = new AtomicReference<>();
        display.syncExec(() -> result.set(read.get()));
        return result.get();
    }

    private static IWorkbenchPage page() {
        IWorkbenchWindow window = PlatformUI.getWorkbench().getActiveWorkbenchWindow();
        if (window == null) {
            IWorkbenchWindow[] all = PlatformUI.getWorkbench().getWorkbenchWindows();
            window = all.length == 0 ? null : all[0];
        }
        return window == null ? null : window.getActivePage();
    }

    /** The text editor the user last worked in, if its file is in the workspace. */
    private static ITextEditor textEditor() {
        IWorkbenchPage page = page();
        IEditorPart editor = page == null ? null : page.getActiveEditor();
        if (editor == null) return null;
        ITextEditor text = editor.getAdapter(ITextEditor.class);
        return text != null && text.getEditorInput() instanceof IFileEditorInput ? text : null;
    }

    private static IFile file(ITextEditor editor) {
        return editor == null ? null : ((IFileEditorInput) editor.getEditorInput()).getFile();
    }

    /**
     * The project folder (UI thread): the active editor's project, else the
     * selected resource's, else the only open project; null otherwise.
     */
    static Path projectDir() {
        IProject project = null;
        IFile file = file(textEditor());
        if (file != null) project = file.getProject();
        if (project == null) {
            IWorkbenchPage page = page();
            ISelection selection = page == null ? null : page.getSelection();
            if (selection instanceof IStructuredSelection) {
                Object first = ((IStructuredSelection) selection).getFirstElement();
                if (first instanceof IAdaptable) {
                    IResource resource = ((IAdaptable) first).getAdapter(IResource.class);
                    if (resource != null) project = resource.getProject();
                }
            }
        }
        if (project == null) {
            List<IProject> open = new ArrayList<>();
            for (IProject p : ResourcesPlugin.getWorkspace().getRoot().getProjects()) {
                if (p.isOpen()) open.add(p);
            }
            if (open.size() == 1) project = open.get(0);
        }
        IPath location = project == null || !project.isOpen() ? null : project.getLocation();
        return location == null ? null : location.toFile().toPath();
    }

    /** The editor context (UI thread), or null when no workspace file is open. */
    static Map<String, Object> editorContext() {
        ITextEditor editor = textEditor();
        IFile file = file(editor);
        IPath location = file == null ? null : file.getLocation();
        if (location == null) return null;
        String path = location.toOSString();
        Map<String, Object> selection = null;
        ISelection sel = editor.getSelectionProvider() == null ? null : editor.getSelectionProvider().getSelection();
        if (sel instanceof ITextSelection && ((ITextSelection) sel).getLength() > 0) {
            ITextSelection t = (ITextSelection) sel;
            IDocument doc = editor.getDocumentProvider().getDocument(editor.getEditorInput());
            int endColumn = 0;
            int endOffset = t.getOffset() + t.getLength();
            if (doc != null) {
                try {
                    endColumn = endOffset - doc.getLineOffset(doc.getLineOfOffset(endOffset));
                } catch (BadLocationException e) {
                    endColumn = 1;
                }
            }
            int start = t.getStartLine() + 1;
            selection = EditorContext.selection(path, start, EditorContext.endLine(t.getStartLine(), t.getEndLine(), endColumn), t.getText());
        }
        List<Map<String, Object>> diagnostics = new ArrayList<>();
        for (IMarker m : markers(file)) {
            diagnostics.add(EditorContext.diagnostic(path, m.getAttribute(IMarker.LINE_NUMBER, 1), severity(m),
                    m.getAttribute(IMarker.MESSAGE, ""), source(m)));
        }
        return EditorContext.of(path, selection, diagnostics);
    }

    private static IMarker[] markers(IResource resource) {
        try {
            return resource.findMarkers(IMarker.PROBLEM, true, IResource.DEPTH_INFINITE);
        } catch (CoreException e) {
            return new IMarker[0];
        }
    }

    static String severity(IMarker m) {
        switch (m.getAttribute(IMarker.SEVERITY, IMarker.SEVERITY_INFO)) {
            case IMarker.SEVERITY_ERROR:
                return "error";
            case IMarker.SEVERITY_WARNING:
                return "warning";
            default:
                return "info";
        }
    }

    private static String source(IMarker m) {
        try {
            String type = m.getType();
            int dot = type.lastIndexOf('.');
            return dot < 0 ? type : type.substring(dot + 1);
        } catch (CoreException e) {
            return null;
        }
    }

    // -- IDE tools (H8) ---------------------------------------------------------------

    static void runIdeTool(String name, Map<String, Object> input, Ide.ToolDone done) {
        String file = Json.string(input, "file_path");
        switch (name) {
            case "getDiagnostics":
                done.done(diagnostics(file), false);
                return;
            case "openFile":
                if (file == null) {
                    done.done("file_path is required.", true);
                    return;
                }
                long line = Json.number(input, "line", -1);
                Display display = PlatformUI.isWorkbenchRunning() ? PlatformUI.getWorkbench().getDisplay() : null;
                if (display == null || display.isDisposed()) {
                    done.done("Eclipse is closing.", true);
                    return;
                }
                display.asyncExec(() -> openFile(file, line, done));
                return;
            default:
                done.done("Unknown IDE tool " + name + ".", true);
        }
    }

    /** Problems of a file, or of the whole workspace: "path:line:1 severity: message (source)". */
    static String diagnostics(String file) {
        IWorkspaceRoot root = ResourcesPlugin.getWorkspace().getRoot();
        IResource target = root;
        if (file != null) {
            IFile f = root.getFileForLocation(IPath.fromOSString(file));
            if (f == null || !f.exists()) return "No problems in this file.";
            target = f;
        }
        List<String> lines = new ArrayList<>();
        int total = 0;
        for (IMarker m : markers(target)) {
            total++;
            if (lines.size() >= MAX_TOOL_LINES) continue;
            IResource r = m.getResource();
            String where = r.getFullPath().makeRelative().toString() + ":" + m.getAttribute(IMarker.LINE_NUMBER, 1) + ":1";
            String source = source(m);
            lines.add(where + " " + severity(m) + ": " + m.getAttribute(IMarker.MESSAGE, "") + (source != null ? " (" + source + ")" : ""));
        }
        if (lines.isEmpty()) return file != null ? "No problems in this file." : "No problems.";
        String more = total > lines.size() ? "\n… " + (total - lines.size()) + " more" : "";
        return String.join("\n", lines) + more;
    }

    private static void openFile(String file, long line, Ide.ToolDone done) {
        IWorkbenchPage page = page();
        if (page == null) {
            done.done("No Eclipse window is open.", true);
            return;
        }
        try {
            IFile f = ResourcesPlugin.getWorkspace().getRoot().getFileForLocation(IPath.fromOSString(file));
            IEditorPart editor = f != null && f.exists() ? IDE.openEditor(page, f, false)
                    : IDE.openEditorOnFileStore(page, org.eclipse.core.filesystem.EFS.getLocalFileSystem().getStore(IPath.fromOSString(file)));
            ITextEditor text = editor == null ? null : editor.getAdapter(ITextEditor.class);
            if (text != null && line > 0) {
                IDocument doc = text.getDocumentProvider().getDocument(text.getEditorInput());
                if (doc != null) {
                    int l = (int) Math.min(line - 1, Math.max(0, doc.getNumberOfLines() - 1));
                    try {
                        text.selectAndReveal(doc.getLineOffset(l), 0);
                    } catch (BadLocationException e) {
                        // the file changed: it's open anyway
                    }
                }
            }
            String shown = f != null ? f.getFullPath().makeRelative().toString() : file;
            done.done("Opened " + shown + " in the editor.", false);
        } catch (PartInitException | RuntimeException e) {
            done.done("Could not open " + file + ": " + e.getMessage(), true);
        }
    }
}
