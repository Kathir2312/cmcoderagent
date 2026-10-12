package cmcoder.idea;

import javax.swing.SwingConstants;

import org.jetbrains.annotations.NotNull;

import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.wm.ToolWindow;
import com.intellij.openapi.wm.ToolWindowFactory;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.content.Content;
import com.intellij.ui.content.ContentFactory;

/** The cmcoder tool window. Phase 7 item 1: a placeholder; item 2 puts the chat in it. */
public final class ChatToolWindowFactory implements ToolWindowFactory, DumbAware {
    @Override
    public void createToolWindowContent(@NotNull Project project, @NotNull ToolWindow toolWindow) {
        JBLabel label = new JBLabel("cmcoder", SwingConstants.CENTER);
        Content content = ContentFactory.getInstance().createContent(label, "", false);
        toolWindow.getContentManager().addContent(content);
    }
}
