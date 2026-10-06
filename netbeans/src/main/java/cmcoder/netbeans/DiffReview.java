package cmcoder.netbeans;

import java.awt.BorderLayout;
import java.awt.FlowLayout;
import java.io.IOException;
import java.io.StringReader;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.HashSet;
import java.util.Map;
import java.util.Set;

import javax.swing.JButton;
import javax.swing.JLabel;
import javax.swing.JPanel;

import org.netbeans.api.diff.DiffController;
import org.netbeans.api.diff.StreamSource;
import org.openide.filesystems.FileUtil;
import org.openide.windows.TopComponent;

import cmcoder.ide.core.Protocol;

/**
 * Proposed changes in NetBeans' diff viewer, in an editor tab, with Accept,
 * Accept Always and Reject above it (H9, H10). Swing thread only.
 */
final class DiffReview {
    static final String ACCEPT = "Accept";
    static final String ALWAYS = "Accept Always";
    static final String REJECT = "Reject";

    private final Session session;
    private final Map<String, Tab> open = new HashMap<>();

    DiffReview(Session session) {
        this.session = session;
    }

    void open(String requestId, Protocol.FileChange change) {
        Tab existing = open.get(requestId);
        if (existing != null && existing.isOpened()) {
            existing.requestActive();
            return;
        }
        try {
            Tab tab = new Tab(requestId, change);
            open.put(requestId, tab);
            tab.open();
            tab.requestActive();
        } catch (IOException | RuntimeException e) {
            Plugin.error("Could not show the change to " + change.path, e);
        }
    }

    void close(String requestId) {
        Tab tab = open.remove(requestId);
        if (tab == null) return;
        tab.answered = true;
        tab.close();
    }

    /** For tests: the request ids with a diff open. */
    Set<String> openRequests() {
        return new HashSet<>(open.keySet());
    }

    /** For tests: presses a button (ACCEPT, ALWAYS, REJECT) of a request's diff. */
    void click(String requestId, String button) {
        Tab tab = open.get(requestId);
        if (tab != null) tab.buttons.get(button).doClick();
    }

    /** One request's diff: the file now (left) and as proposed (right). */
    private final class Tab extends TopComponent {
        private static final long serialVersionUID = 1L;
        final transient Map<String, JButton> buttons = new HashMap<>();
        final String requestId;
        boolean answered;

        Tab(String requestId, Protocol.FileChange change) throws IOException {
            this.requestId = requestId;
            String name = name(change);
            setName(name + (change.before == null ? " (new)" : "") + " ↔ " + Brand.product());
            setToolTipText(Brand.product() + " proposes " + (change.before == null ? "a new file: " : "a change to ") + change.path);
            String mime = mime(change.path);
            StreamSource now = StreamSource.createSource(name, change.before == null ? "(no file yet)" : name + " (now)", mime,
                    new StringReader(change.before == null ? "" : change.before));
            StreamSource proposed = StreamSource.createSource(name, name + " (proposed by " + Brand.product() + ")", mime,
                    new StringReader(change.after));
            DiffController diff = DiffController.createEnhanced(now, proposed);
            setLayout(new BorderLayout());
            JPanel bar = new JPanel(new FlowLayout(FlowLayout.LEFT));
            bar.add(new JLabel(Brand.product() + " wants to " + (change.before == null ? "create " : "change ") + change.path));
            button(bar, ACCEPT, true, false);
            button(bar, ALWAYS, true, true);
            button(bar, REJECT, false, false);
            add(bar, BorderLayout.NORTH);
            add(diff.getJComponent(), BorderLayout.CENTER);
        }

        private void button(JPanel bar, String text, boolean allow, boolean remember) {
            JButton b = new JButton(text);
            b.addActionListener(e -> {
                if (answered) return;
                answered = true;
                session.answer(requestId, allow, remember);
            });
            buttons.put(text, b);
            bar.add(b);
        }

        @Override
        public int getPersistenceType() {
            return PERSISTENCE_NEVER;
        }

        @Override
        protected void componentClosed() {
            // Closed by the user without an answer: the chat card still asks.
            open.remove(requestId, this);
        }
    }

    static String name(Protocol.FileChange change) {
        Path p = Paths.get(change.path).getFileName();
        return p == null ? change.path : p.toString();
    }

    /** The highlighting NetBeans uses for the file's type (asked of a file of that name in memory). */
    private static String mime(String path) {
        try {
            String name = Paths.get(path).getFileName().toString();
            String mime = FileUtil.createMemoryFileSystem().getRoot().createData(name).getMIMEType();
            return mime == null ? "text/plain" : mime;
        } catch (IOException | RuntimeException e) {
            return "text/plain";
        }
    }
}
