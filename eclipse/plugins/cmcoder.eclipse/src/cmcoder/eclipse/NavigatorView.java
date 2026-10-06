package cmcoder.eclipse;

import org.eclipse.swt.widgets.Composite;
import org.eclipse.ui.part.ViewPart;

/** The Agent Navigator (H15): the current turn's agents, from the session. */
public final class NavigatorView extends ViewPart {
    private Panel panel;

    @Override
    public void createPartControl(Composite parent) {
        setPartName(Brand.product() + ": Agent Navigator");
        Session session = Session.get();
        panel = Panel.create(parent, "navigator", json -> session.run(h -> h.onNavigatorMessage(json)));
        if (panel != null) {
            session.navigatorOpened(panel);
            panel.load();
        }
    }

    public Panel panel() {
        return panel;
    }

    @Override
    public void setFocus() {
        if (panel != null) panel.setFocus();
    }

    @Override
    public void dispose() {
        if (panel != null) Session.get().navigatorClosed(panel);
        super.dispose();
    }
}
