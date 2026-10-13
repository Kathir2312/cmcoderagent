package cmcoder.eclipse;

import java.io.ByteArrayInputStream;
import java.io.InputStream;
import java.lang.reflect.InvocationTargetException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;

import org.eclipse.compare.CompareConfiguration;
import org.eclipse.compare.CompareEditorInput;
import org.eclipse.compare.CompareUI;
import org.eclipse.compare.IEncodedStreamContentAccessor;
import org.eclipse.compare.ITypedElement;
import org.eclipse.compare.structuremergeviewer.DiffNode;
import org.eclipse.compare.structuremergeviewer.Differencer;
import org.eclipse.core.runtime.IProgressMonitor;
import org.eclipse.swt.SWT;
import org.eclipse.swt.graphics.Image;
import org.eclipse.swt.layout.GridData;
import org.eclipse.swt.layout.GridLayout;
import org.eclipse.swt.widgets.Button;
import org.eclipse.swt.widgets.Composite;
import org.eclipse.swt.widgets.Control;
import org.eclipse.swt.widgets.Label;
import org.eclipse.ui.IEditorPart;
import org.eclipse.ui.IEditorReference;
import org.eclipse.ui.IWorkbenchPage;
import org.eclipse.ui.IWorkbenchWindow;
import org.eclipse.ui.PlatformUI;

import cmcoder.ide.core.Protocol;

/**
 * Proposed changes in Eclipse's compare editor, with Accept, Always and Reject
 * above the diff (H9, H10). UI thread only.
 */
final class DiffReview {
    private final Session session;
    private final Map<String, Input> open = new HashMap<>();

    DiffReview(Session session) {
        this.session = session;
    }

    void open(String requestId, Protocol.FileChange change) {
        Input existing = open.get(requestId);
        IWorkbenchPage page = page();
        if (page == null) return;
        if (existing != null) {
            IEditorPart editor = page.findEditor(existing);
            if (editor != null) {
                page.activate(editor);
                return;
            }
        }
        Input input = new Input(requestId, change);
        open.put(requestId, input);
        CompareUI.openCompareEditorOnPage(input, page);
    }

    void close(String requestId) {
        Input input = open.remove(requestId);
        if (input == null) return;
        input.answered = true;
        IWorkbenchPage page = page();
        if (page == null) return;
        for (IEditorReference ref : page.getEditorReferences()) {
            IEditorPart editor = ref.getEditor(false);
            if (editor != null && editor.getEditorInput() == input) page.closeEditor(editor, false);
        }
    }

    /** For tests: the request ids with a diff open. */
    java.util.Set<String> openRequests() {
        return new java.util.HashSet<>(open.keySet());
    }

    private static IWorkbenchPage page() {
        IWorkbenchWindow window = PlatformUI.getWorkbench().getActiveWorkbenchWindow();
        if (window == null && PlatformUI.getWorkbench().getWorkbenchWindows().length > 0) {
            window = PlatformUI.getWorkbench().getWorkbenchWindows()[0];
        }
        return window == null ? null : window.getActivePage();
    }

    /** One request's diff: the file now (left) and as proposed (right), both read-only. */
    private final class Input extends CompareEditorInput {
        final String requestId;
        final Protocol.FileChange change;
        boolean answered;

        Input(String requestId, Protocol.FileChange change) {
            super(configuration(change));
            this.requestId = requestId;
            this.change = change;
            String name = name(change);
            setTitle(change.before == null ? name + " (new file, proposed by " + Brand.product() + ")"
                    : name + " ↔ proposed by " + Brand.product());
        }

        @Override
        protected Object prepareInput(IProgressMonitor monitor) throws InvocationTargetException, InterruptedException {
            String name = name(change);
            return new DiffNode(null, change.before == null ? Differencer.ADDITION : Differencer.CHANGE, null,
                    new Side(name, change.before == null ? "" : change.before),
                    new Side(name, change.after));
        }

        @Override
        public Control createContents(Composite parent) {
            Composite all = new Composite(parent, SWT.NONE);
            GridLayout layout = new GridLayout(1, false);
            layout.marginWidth = 0;
            layout.marginHeight = 0;
            all.setLayout(layout);
            Composite bar = new Composite(all, SWT.NONE);
            bar.setLayoutData(new GridData(SWT.FILL, SWT.TOP, true, false));
            bar.setLayout(new GridLayout(4, false));
            Label label = new Label(bar, SWT.WRAP);
            label.setText(Brand.product() + " wants to " + (change.before == null ? "create " : "change ") + change.path);
            label.setLayoutData(new GridData(SWT.FILL, SWT.CENTER, true, false));
            button(bar, "Accept", "cmcoder.eclipse.diff.accept", true, false);
            button(bar, "Accept Always", "cmcoder.eclipse.diff.always", true, true);
            button(bar, "Reject", "cmcoder.eclipse.diff.reject", false, false);
            Control diff = super.createContents(all);
            diff.setLayoutData(new GridData(SWT.FILL, SWT.FILL, true, true));
            return all;
        }

        private void button(Composite bar, String text, String testId, boolean allow, boolean remember) {
            Button b = new Button(bar, SWT.PUSH);
            b.setText(text);
            b.setData("org.eclipse.swtbot.widget.key", testId);
            b.addListener(SWT.Selection, e -> {
                if (answered) return;
                answered = true;
                session.answer(requestId, allow, remember);
            });
        }

        @Override
        public boolean isSaveNeeded() {
            return false;
        }
    }

    private static CompareConfiguration configuration(Protocol.FileChange change) {
        CompareConfiguration c = new CompareConfiguration();
        // Now on the left, proposed on the right, even where "swap left and right" is on.
        c.setProperty(CompareConfiguration.MIRRORED, Boolean.FALSE);
        c.setLeftEditable(false);
        c.setRightEditable(false);
        c.setLeftLabel(change.before == null ? "(no file yet)" : name(change) + " (now)");
        c.setRightLabel(name(change) + " (proposed by " + Brand.product() + ")");
        return c;
    }

    static String name(Protocol.FileChange change) {
        Path p = Paths.get(change.path).getFileName();
        return p == null ? change.path : p.toString();
    }

    /** One side's text; the type is the file's extension, so the right highlighter is used. */
    private static final class Side implements ITypedElement, IEncodedStreamContentAccessor {
        private final String name;
        private final String text;

        Side(String name, String text) {
            this.name = name;
            this.text = text;
        }

        @Override
        public String getName() {
            return name;
        }

        @Override
        public Image getImage() {
            return null;
        }

        @Override
        public String getType() {
            int dot = name.lastIndexOf('.');
            return dot < 0 ? ITypedElement.TEXT_TYPE : name.substring(dot + 1);
        }

        @Override
        public InputStream getContents() {
            return new ByteArrayInputStream(text.getBytes(StandardCharsets.UTF_8));
        }

        @Override
        public String getCharset() {
            return StandardCharsets.UTF_8.name();
        }
    }
}
