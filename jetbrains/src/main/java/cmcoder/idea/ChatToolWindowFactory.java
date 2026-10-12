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
import com.intellij.ui.jcef.JBCefApp;

/** The cmcoder tool window: the shared chat page in JCEF, one conversation per project. */
public final class ChatToolWindowFactory implements ToolWindowFactory, DumbAware {
    @Override
    public void createToolWindowContent(@NotNull Project project, @NotNull ToolWindow toolWindow) {
        Session session = Session.get(project);
        Content content;
        if (!JBCefApp.isSupported()) {
            content = message(Brand.product() + " shows its chat in the IDE's built-in browser (JCEF), which this IDE's "
                    + "runtime doesn't have. Choose a JetBrains Runtime with JCEF: Help → Find Action → "
                    + "\"Choose Boot Java Runtime for the IDE\". The terminal (cmcoder) works meanwhile.");
        } else {
            try {
                Panel panel = Panel.create("chat", json -> session.run(h -> h.onPanelMessage(json)), session);
                session.chatOpened(panel);
                Disposer.register(panel, () -> session.chatClosed(panel));
                content = ContentFactory.getInstance().createContent(panel.component(), "", false);
                content.setDisposer(panel);
            } catch (IOException | RuntimeException e) {
                Plugin.error("Could not open the " + Brand.product() + " chat", e);
                content = message(Brand.product() + " could not prepare its chat: " + e.getMessage());
            }
        }
        toolWindow.getContentManager().addContent(content);
    }

    private static Content message(String text) {
        JBTextArea area = new JBTextArea(text);
        area.setEditable(false);
        area.setLineWrap(true);
        area.setWrapStyleWord(true);
        JComponent component = new JBScrollPane(area);
        return ContentFactory.getInstance().createContent(component, "", false);
    }
}
