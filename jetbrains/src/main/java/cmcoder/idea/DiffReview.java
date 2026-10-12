package cmcoder.idea;

import java.awt.Dimension;
import java.awt.event.ActionEvent;
import java.nio.file.Path;
import java.util.HashMap;
import java.util.Map;

import javax.swing.Action;
import javax.swing.JComponent;

import com.intellij.diff.DiffContentFactory;
import com.intellij.diff.DiffManager;
import com.intellij.diff.DiffRequestPanel;
import com.intellij.diff.contents.DiffContent;
import com.intellij.diff.requests.SimpleDiffRequest;
import com.intellij.openapi.fileTypes.FileType;
import com.intellij.openapi.fileTypes.FileTypeManager;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.util.text.StringUtil;

import cmcoder.ide.core.Protocol;

/**
 * Proposed changes in the IDE's diff viewer, in a window of their own with
 * Accept, Accept Always and Reject (H9, H10). Closing the window answers
 * nothing: the chat still asks. UI thread only.
 */
final class DiffReview {
    static final String ACCEPT = "Accept";
    static final String ALWAYS = "Accept Always";
    static final String REJECT = "Reject";

    private final Project project;
    private final Session session;
    private final Map<String, Review> open = new HashMap<>();

    DiffReview(Project project, Session session) {
        this.project = project;
        this.session = session;
    }

    void open(String requestId, Protocol.FileChange change) {
        if (project.isDisposed()) return;
        Review existing = open.get(requestId);
        if (existing != null && existing.isShowing()) {
            existing.toFront();
            return;
        }
        try {
            Review review = new Review(requestId, change);
            open.put(requestId, review);
            review.show();
        } catch (RuntimeException e) {
            Plugin.error("Could not show the change to " + change.path, e);
        }
    }

    /** The request was answered elsewhere (the chat) or the turn ended. */
    void close(String requestId) {
        Review review = open.remove(requestId);
        if (review == null) return;
        review.answered = true;
        if (!review.isDisposed()) review.close(DialogWrapper.CANCEL_EXIT_CODE);
    }

    void closeAll() {
        for (String id : open.keySet().toArray(new String[0])) close(id);
    }

    static String name(Protocol.FileChange change) {
        Path p = Path.of(change.path).getFileName();
        return p == null ? change.path : p.toString();
    }

    /** One request's diff: the file now (left) and as proposed (right). */
    private final class Review extends DialogWrapper {
        final String requestId;
        final DiffRequestPanel panel;
        boolean answered;

        Review(String requestId, Protocol.FileChange change) {
            super(project, true, IdeModalityType.MODELESS);
            this.requestId = requestId;
            String name = name(change);
            boolean created = change.before == null;
            setTitle(Brand.product() + " wants to " + (created ? "create " : "change ") + change.path);
            panel = DiffManager.getInstance().createRequestPanel(project, getDisposable(), null);
            FileType type = FileTypeManager.getInstance().getFileTypeByFileName(name);
            DiffContentFactory contents = DiffContentFactory.getInstance();
            DiffContent now = contents.create(project, created ? "" : StringUtil.convertLineSeparators(change.before), type);
            DiffContent proposed = contents.create(project, StringUtil.convertLineSeparators(change.after), type);
            panel.setRequest(new SimpleDiffRequest(getTitle(), now, proposed,
                    created ? "(no file yet)" : name + " (now)", name + " (proposed by " + Brand.product() + ")"));
            setOKButtonText(ACCEPT);
            init();
        }

        @Override
        protected JComponent createCenterPanel() {
            JComponent c = panel.getComponent();
            c.setPreferredSize(new Dimension(1000, 640));
            return c;
        }

        @Override
        protected Action[] createActions() {
            return new Action[] {getOKAction(), new Answer(ALWAYS, true, true), new Answer(REJECT, false, false)};
        }

        @Override
        protected void doOKAction() {
            answer(true, false);
            super.doOKAction();
        }

        @Override
        protected void dispose() {
            open.remove(requestId, this);
            super.dispose();
        }

        private void answer(boolean allow, boolean remember) {
            if (answered) return;
            answered = true;
            session.answer(requestId, allow, remember);
        }

        private final class Answer extends DialogWrapperAction {
            private final boolean allow;
            private final boolean remember;

            Answer(String text, boolean allow, boolean remember) {
                super(text);
                this.allow = allow;
                this.remember = remember;
            }

            @Override
            protected void doAction(ActionEvent e) {
                answer(allow, remember);
                close(allow ? OK_EXIT_CODE : CANCEL_EXIT_CODE);
            }
        }
    }
}
