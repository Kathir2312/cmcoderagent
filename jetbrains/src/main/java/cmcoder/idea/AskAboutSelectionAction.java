package cmcoder.idea;

import org.jetbrains.annotations.NotNull;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.actionSystem.CommonDataKeys;
import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;

import cmcoder.ide.core.Host;

/** "Ask cmcoder About Selection" in the editor's menu and on Ctrl+Alt+K (H14): the chat, with a prompt to finish. */
public final class AskAboutSelectionAction extends AnAction implements DumbAware {
    @Override
    public void actionPerformed(@NotNull AnActionEvent e) {
        Project project = e.getProject();
        if (project == null) return;
        Session session = Session.get(project);
        session.showChat(); // the chat opens (and cmcoder starts) if it wasn't open yet
        session.run(Host::askAboutSelection);
    }

    @Override
    public void update(@NotNull AnActionEvent e) {
        e.getPresentation().setEnabledAndVisible(e.getProject() != null && e.getData(CommonDataKeys.EDITOR) != null);
        e.getPresentation().setText("Ask " + Brand.product() + " About Selection");
    }

    @Override
    public @NotNull ActionUpdateThread getActionUpdateThread() {
        return ActionUpdateThread.BGT;
    }
}
