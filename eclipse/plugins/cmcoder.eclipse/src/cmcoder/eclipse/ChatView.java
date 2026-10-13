package cmcoder.eclipse;

import org.eclipse.jface.action.Action;
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
        if (panel != null) {
            session.chatOpened(panel, getSite().getWorkbenchWindow());
            panel.load();
        }
        // Code search's state in the view's toolbar; a click opens its menu (H16).
        codeSearchItem = new Action("Code search") {
            @Override
            public void run() {
                CodeSearchUi.menu(session.codeSearch());
            }
        };
        codeSearchItem.setId("cmcoder.eclipse.codeSearch");
        getViewSite().getActionBars().getToolBarManager().add(codeSearchItem);
        getViewSite().getActionBars().updateActionBars();
        stopCodeSearch = session.onCodeSearchChange(() -> parent.getDisplay().asyncExec(this::showCodeSearch));
        showCodeSearch();
    }

    private Action codeSearchItem;
    private Runnable stopCodeSearch;

    private void showCodeSearch() {
        if (codeSearchItem == null || panel != null && panel.disposed()) return;
        String text = Session.get().codeSearch().text();
        codeSearchItem.setText(text == null ? "Code search" : text);
        codeSearchItem.setToolTipText(text == null ? "Code search (when " + Brand.product() + " is running)"
                : Session.get().codeSearch().tooltip());
        getViewSite().getActionBars().getToolBarManager().update(true);
    }

    /** For tests: the code search item's text. */
    String codeSearchText() {
        return codeSearchItem == null ? null : codeSearchItem.getText();
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
        if (stopCodeSearch != null) stopCodeSearch.run();
        if (panel != null) Session.get().chatClosed(panel);
        super.dispose();
    }
}
