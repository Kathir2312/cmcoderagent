package cmcoder.netbeans;

import java.awt.BorderLayout;

import javax.swing.JComponent;

import org.openide.windows.Mode;
import org.openide.windows.TopComponent;
import org.openide.windows.WindowManager;

/** The Agent Navigator (H15): the shared navigator page in an editor tab, live during the turn. */
public final class NavigatorWindow extends TopComponent {
    private static final long serialVersionUID = 1L;
    private static NavigatorWindow instance;
    private transient Panel panel;

    private NavigatorWindow() {
        setName(Brand.product() + ": Agent Navigator");
        setLayout(new BorderLayout());
    }

    /** Opens (or shows) the navigator. Swing thread. */
    static NavigatorWindow showIt() {
        if (instance == null) instance = new NavigatorWindow();
        if (!instance.isOpened()) {
            Mode editor = WindowManager.getDefault().findMode("editor");
            if (editor != null) editor.dockInto(instance);
            instance.open();
        }
        instance.requestActive();
        return instance;
    }

    @Override
    public int getPersistenceType() {
        return PERSISTENCE_NEVER;
    }

    @Override
    protected void componentOpened() {
        Session session = Session.get();
        removeAll();
        panel = Panel.create("navigator", json -> session.run(h -> h.onNavigatorMessage(json)), this::showError);
        if (panel != null) {
            add(panel, BorderLayout.CENTER);
            session.navigatorOpened(panel);
            panel.load();
        }
        revalidate();
    }

    private void showError(JComponent message) {
        add(message, BorderLayout.CENTER);
    }

    public Panel panel() {
        return panel;
    }

    @Override
    protected void componentClosed() {
        if (panel != null) {
            Session.get().navigatorClosed(panel);
            panel.close();
        }
        panel = null;
        removeAll();
    }
}
