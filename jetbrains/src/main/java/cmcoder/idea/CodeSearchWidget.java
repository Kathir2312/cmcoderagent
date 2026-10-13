package cmcoder.idea;

import java.awt.Component;
import java.awt.event.MouseEvent;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.util.Disposer;
import com.intellij.openapi.util.text.StringUtil;
import com.intellij.openapi.wm.StatusBar;
import com.intellij.openapi.wm.StatusBarWidget;
import com.intellij.openapi.wm.StatusBarWidgetFactory;
import com.intellij.util.Consumer;

import cmcoder.ide.core.CodeSearch;

/**
 * Code search's state in the status bar (H16): "Code search: 1,234 files",
 * "Indexing 10/200 files", …; a click opens its menu or the set-up.
 */
public final class CodeSearchWidget implements StatusBarWidgetFactory {
    /** The id in plugin.xml. */
    static final String ID = "cmcoder.codeSearch";

    @Override
    public @NotNull String getId() {
        return ID;
    }

    @Override
    public @NotNull String getDisplayName() {
        return Brand.product() + " Code Search";
    }

    @Override
    public boolean isAvailable(@NotNull Project project) {
        return true;
    }

    @Override
    public @NotNull StatusBarWidget createWidget(@NotNull Project project) {
        return new Widget(project);
    }

    @Override
    public void disposeWidget(@NotNull StatusBarWidget widget) {
        Disposer.dispose(widget);
    }

    @Override
    public boolean canBeEnabledOn(@NotNull StatusBar statusBar) {
        return true;
    }

    private static final class Widget implements StatusBarWidget, StatusBarWidget.TextPresentation {
        private final Project project;
        private volatile StatusBar bar;
        private Runnable stop;

        Widget(Project project) {
            this.project = project;
        }

        private CodeSearch codeSearch() {
            return Session.get(project).codeSearch();
        }

        @Override
        public @NotNull String ID() {
            return ID;
        }

        @Override
        public @Nullable WidgetPresentation getPresentation() {
            return this;
        }

        @Override
        public void install(@NotNull StatusBar statusBar) {
            bar = statusBar;
            stop = Session.get(project).onCodeSearchChange(() -> ApplicationManager.getApplication().invokeLater(() -> {
                StatusBar b = bar;
                if (b != null && !project.isDisposed()) b.updateWidget(ID);
            }));
        }

        @Override
        public void dispose() {
            bar = null;
            if (stop != null) stop.run();
            stop = null;
        }

        @Override
        public @NotNull String getText() {
            String text = codeSearch().text();
            return text == null ? "Code search" : text;
        }

        @Override
        public float getAlignment() {
            return Component.CENTER_ALIGNMENT;
        }

        @Override
        public @Nullable String getTooltipText() {
            if (codeSearch().text() == null) return "Code search (when " + Brand.product() + " is running)";
            String tip = codeSearch().tooltip();
            // One fact per line: as HTML, where a tooltip breaks lines.
            return tip.contains("\n") ? "<html>" + StringUtil.escapeXmlEntities(tip).replace("\n", "<br>") + "</html>" : tip;
        }

        @Override
        public @Nullable Consumer<MouseEvent> getClickConsumer() {
            return e -> {
                if (!project.isDisposed()) CodeSearchUi.menu(project, codeSearch());
            };
        }
    }
}
