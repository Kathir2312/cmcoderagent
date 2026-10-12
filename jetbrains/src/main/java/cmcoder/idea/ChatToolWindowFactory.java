package cmcoder.idea;

import java.io.IOException;

import javax.swing.JComponent;

import org.jetbrains.annotations.NotNull;

import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.util.Disposer;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextArea;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;

/**
 * The cmcoder tool window: the shared chat page in JCEF, one conversation per
 * project, and the project's log (H22) in a second tab.
 */
public final class ChatToolWindowFactory implements ToolWindowFactory, DumbAware {
    /** The chat's tab. */
    static final String CHAT_TAB = "Chat";

    @Override
    public void init(@NotNull ToolWindow toolWindow) {
        toolWindow.setStripeTitle(Brand.product()); // the id stays "cmcoder"; the brand's name is shown
    }

    @Override
    public void createToolWindowContent(@NotNull Project project, @NotNull ToolWindow toolWindow) {
        Session session = Session.get(project);
        ContentFactory factory = ContentFactory.getInstance();
        Content content;
        if (!Plugin.jcef()) {
            content = factory.createContent(messageComponent(Brand.product() + " shows its chat in the IDE's built-in browser (JCEF), "
                    + "which this IDE's runtime doesn't have. Choose a JetBrains Runtime with JCEF: Help → Find Action → "
                    + "\"Choose Boot Java Runtime for the IDE\". The terminal (cmcoder) works meanwhile."), CHAT_TAB, false);
        } else {
            try {
                Panel panel = Panel.create("chat", json -> session.run(h -> h.onPanelMessage(json)), session);
                session.chatOpened(panel);
                Disposer.register(panel, () -> session.chatClosed(panel));
                content = factory.createContent(panel.component(), CHAT_TAB, false);
                content.setDisposer(panel);
            } catch (IOException | RuntimeException e) {
                Plugin.error("Could not open the " + Brand.product() + " chat", e);
                content = factory.createContent(messageComponent(Brand.product() + " could not prepare its chat: " + e.getMessage()),
                        CHAT_TAB, false);
            }
        }
        toolWindow.getContentManager().addContent(content);

        LogView log = new LogView(session);
        Content logContent = factory.createContent(log.component(), LogView.TAB, false);
        logContent.setDisposer(log);
        toolWindow.getContentManager().addContent(logContent);
        toolWindow.getContentManager().setSelectedContent(content);
    }

    /** A read-only message where a page would be. */
    static JComponent messageComponent(String text) {
        JBTextArea area = new JBTextArea(text);
        area.setEditable(false);
        area.setLineWrap(true);
        area.setWrapStyleWord(true);
        return new JBScrollPane(area);
    }
}
