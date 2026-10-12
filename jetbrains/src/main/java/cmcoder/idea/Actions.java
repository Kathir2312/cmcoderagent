package cmcoder.idea;

import java.util.Collections;

import org.jetbrains.annotations.NotNull;

import com.intellij.openapi.actionSystem.ActionUpdateThread;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.AnActionEvent;
import com.intellij.openapi.options.ShowSettingsUtil;
import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;

import cmcoder.ide.core.Host;

/** The plugin's menu (Tools → cmcoder); the texts are in plugin.xml, with the brand's name. */
public final class Actions {
    private Actions() {}

    /** An action for the current project; off without one. */
    abstract static class ProjectAction extends AnAction implements DumbAware {
        @Override
        public void actionPerformed(@NotNull AnActionEvent e) {
            Project project = e.getProject();
            if (project != null && !project.isDisposed()) perform(project);
        }

        abstract void perform(Project project);

        @Override
        public void update(@NotNull AnActionEvent e) {
            e.getPresentation().setEnabled(e.getProject() != null);
        }

        @Override
        public @NotNull ActionUpdateThread getActionUpdateThread() {
            return ActionUpdateThread.BGT;
        }
    }

    public static final class OpenChat extends ProjectAction {
        @Override
        void perform(Project project) {
            Session.get(project).showChat();
        }
    }

    public static final class NewConversation extends ProjectAction {
        @Override
        void perform(Project project) {
            Session session = Session.get(project);
            session.showChat();
            session.run(h -> h.newConversation(Collections.emptyList()));
        }
    }

    public static final class Stop extends ProjectAction {
        @Override
        void perform(Project project) {
            Session.get(project).run(Host::interrupt);
        }
    }

    public static final class OpenNavigator extends ProjectAction {
        @Override
        void perform(Project project) {
            NavigatorEditorProvider.open(project);
        }
    }

    public static final class CodeSearch extends ProjectAction {
        @Override
        void perform(Project project) {
            CodeSearchUi.menu(project, Session.get(project).codeSearch());
        }
    }

    public static final class ShowLog extends ProjectAction {
        @Override
        void perform(Project project) {
            Session.get(project).showLog();
        }
    }

    public static final class CopyDiagnostics extends ProjectAction {
        @Override
        void perform(Project project) {
            Diagnostics.copy(project);
        }
    }

    public static final class OpenSettings extends ProjectAction {
        @Override
        void perform(Project project) {
            ShowSettingsUtil.getInstance().showSettingsDialog(project, SettingsPage.class);
        }
    }
}
