package cmcoder.eclipse;

import org.eclipse.swt.widgets.Composite;
import org.eclipse.ui.part.ViewPart;

/** The chat (H5): the shared chat page, talking to the session. */
public final class ChatView extends ViewPart {
    private Panel panel;

    @Override
    public void createPartControl(Composite parent) {
        setPartName(Brand.product());
        Session session = Session.get();
        panel = Panel.create(parent, "chat", json -> session.run(h -> h.onPanelMessage(json)));
        if (panel != null) session.chatOpened(panel, getSite().getWorkbenchWindow());
    }

    /** For tests: the page in this view, or null if no browser could start. */
    public Panel panel() {
        return panel;
    }

    @Override
    public void setFocus() {
        if (panel != null) panel.setFocus();
    }

    @Override
    public void dispose() {
        if (panel != null) Session.get().chatClosed(panel);
        super.dispose();
    }
}
