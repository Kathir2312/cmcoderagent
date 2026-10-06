package cmcoder.netbeans;

import java.awt.BorderLayout;

import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JToolBar;
import javax.swing.SwingUtilities;

import org.openide.awt.ActionID;
import org.openide.awt.ActionReference;
import org.openide.awt.ActionReferences;
import org.openide.windows.TopComponent;
import org.openide.windows.WindowManager;

/** The chat (H5): the shared chat page, talking to the session; on the right side. */
@TopComponent.Description(preferredID = ChatWindow.ID, iconBase = "cmcoder/netbeans/icon16.png",
        persistenceType = TopComponent.PERSISTENCE_NEVER)
@TopComponent.Registration(mode = "properties", openAtStartup = false)
@ActionID(category = "Window", id = "cmcoder.netbeans.OpenChat")
@ActionReferences({
    @ActionReference(path = "Menu/Tools/cmcoder", position = 100),
    @ActionReference(path = "Menu/Window", position = 340),
    @ActionReference(path = "Shortcuts", name = "DA-J"),
})
@TopComponent.OpenActionRegistration(displayName = "Open cmcoder Chat", preferredID = ChatWindow.ID)
public final class ChatWindow extends TopComponent {
    private static final long serialVersionUID = 1L;
    static final String ID = "CmcoderChat";

    private transient Panel panel;
    private JButton codeSearch;
    private transient Runnable stopCodeSearch;

    public ChatWindow() {
        setName(Brand.product());
        setToolTipText(Brand.product() + ": chat");
        setLayout(new BorderLayout());
    }

    /** Opens (or shows) the chat. Swing thread. */
    static ChatWindow showIt() {
        // The open one first: one that isn't saved between sessions isn't always found by its id.
        ChatWindow chat = find();
        if (chat == null) {
            TopComponent tc = WindowManager.getDefault().findTopComponent(ID);
            chat = tc instanceof ChatWindow ? (ChatWindow) tc : new ChatWindow();
        }
        if (!chat.isOpened()) chat.open();
        chat.requestActive();
        return chat;
    }

    /** The open chat, or null. Swing thread. */
    static ChatWindow find() {
        for (TopComponent tc : TopComponent.getRegistry().getOpened()) {
            if (tc instanceof ChatWindow) return (ChatWindow) tc;
        }
        return null;
    }

    @Override
    protected void componentOpened() {
        Session session = Session.get();
        removeAll();
        JToolBar bar = new JToolBar();
        bar.setFloatable(false);
        codeSearch = new JButton("Code search");
        codeSearch.addActionListener(e -> CodeSearchUi.menu(session.codeSearch()));
        bar.add(codeSearch);
        add(bar, BorderLayout.NORTH);
        panel = Panel.create("chat", json -> session.run(h -> h.onPanelMessage(json)), this::showError);
        if (panel != null) {
            add(panel, BorderLayout.CENTER);
            session.chatOpened(panel);
            panel.load();
        }
        // Code search's state on the toolbar; a click opens its menu (H16).
        stopCodeSearch = session.onCodeSearchChange(() -> SwingUtilities.invokeLater(this::showCodeSearch));
        showCodeSearch();
        revalidate();
    }

    private void showError(JComponent message) {
        add(message, BorderLayout.CENTER);
    }

    private void showCodeSearch() {
        if (codeSearch == null) return;
        String text = Session.get().codeSearch().text();
        codeSearch.setText(text == null ? "Code search" : text);
        codeSearch.setToolTipText(text == null ? "Code search (when " + Brand.product() + " is running)"
                : Session.get().codeSearch().tooltip());
    }

    /** For tests: the code search item's text. */
    String codeSearchText() {
        return codeSearch == null ? null : codeSearch.getText();
    }

    /** The page in this window, or null if it couldn't start. */
    public Panel panel() {
        return panel;
    }

    @Override
    protected void componentClosed() {
        if (stopCodeSearch != null) stopCodeSearch.run();
        if (panel != null) {
            Session.get().chatClosed(panel);
            panel.close();
        }
        panel = null;
        removeAll();
    }

    @Override
    protected void componentActivated() {
        if (panel != null) panel.requestFocusInWindow();
    }
}
