package cmcoder.idea;

import java.beans.PropertyChangeListener;
import java.io.IOException;

import javax.swing.Icon;
import javax.swing.JComponent;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import com.intellij.ide.FileIconProvider;
import com.intellij.openapi.fileEditor.FileEditor;
import com.intellij.openapi.fileEditor.FileEditorManager;
import com.intellij.openapi.fileEditor.FileEditorPolicy;
import com.intellij.openapi.fileEditor.FileEditorProvider;
import com.intellij.openapi.fileEditor.FileEditorState;
import com.intellij.openapi.fileTypes.UnknownFileType;
import com.intellij.openapi.project.DumbAware;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.util.Disposer;
import com.intellij.openapi.util.IconLoader;
import com.intellij.openapi.util.UserDataHolderBase;
import com.intellij.openapi.vfs.VirtualFile;
import com.intellij.testFramework.LightVirtualFile;

/**
 * The Agent Navigator (H15): the shared navigator page in an editor tab, live
 * during the turn. The tab shows a file that exists only in memory, one per
 * project; this provider is the only editor for it.
 */
public final class NavigatorEditorProvider implements FileEditorProvider, DumbAware {
    static final Icon ICON = IconLoader.getIcon("/icons/toolWindow.svg", NavigatorEditorProvider.class);

    /** Opens (or shows) the project's navigator tab. UI thread. */
    static void open(Project project) {
        if (project.isDisposed()) return;
        FileEditorManager.getInstance(project).openFile(Session.get(project).navigatorFile(), true);
    }

    @Override
    public boolean accept(@NotNull Project project, @NotNull VirtualFile file) {
        return file instanceof NavigatorFile;
    }

    @Override
    public @NotNull FileEditor createEditor(@NotNull Project project, @NotNull VirtualFile file) {
        return new NavigatorEditor(project, file);
    }

    @Override
    public @NotNull String getEditorTypeId() {
        return "cmcoder-navigator";
    }

    @Override
    public @NotNull FileEditorPolicy getPolicy() {
        return FileEditorPolicy.HIDE_DEFAULT_EDITOR;
    }

    /** The tab's file: in memory only, nothing to save, not reopened when the IDE starts again. */
    static final class NavigatorFile extends LightVirtualFile {
        NavigatorFile() {
            super(Brand.product() + " Agent Navigator", UnknownFileType.INSTANCE, "");
            setWritable(false);
        }
    }

    /** The tab's icon: the brand's. */
    public static final class TabIcon implements FileIconProvider {
        @Override
        public @Nullable Icon getIcon(@NotNull VirtualFile file, int flags, @Nullable Project project) {
            return file instanceof NavigatorFile ? ICON : null;
        }
    }

    private static final class NavigatorEditor extends UserDataHolderBase implements FileEditor {
        private final VirtualFile file;
        private final JComponent component;

        NavigatorEditor(Project project, VirtualFile file) {
            this.file = file;
            Session session = Session.get(project);
            JComponent shown;
            if (!Plugin.jcef()) {
                shown = ChatToolWindowFactory.messageComponent(Brand.product() + " shows the Agent Navigator in the IDE's built-in "
                        + "browser (JCEF), which this IDE's runtime doesn't have (see the chat for how to fix it).");
            } else {
                try {
                    Panel panel = Panel.create("navigator", json -> session.run(h -> h.onNavigatorMessage(json)), this);
                    session.navigatorOpened(panel);
                    Disposer.register(panel, () -> session.navigatorClosed(panel));
                    shown = panel.component();
                } catch (IOException | RuntimeException e) {
                    Plugin.error("Could not open the Agent Navigator", e);
                    shown = ChatToolWindowFactory.messageComponent(Brand.product() + " could not prepare the Agent Navigator: "
                            + e.getMessage());
                }
            }
            component = shown;
        }

        @Override
        public @NotNull JComponent getComponent() {
            return component;
        }

        @Override
        public @Nullable JComponent getPreferredFocusedComponent() {
            return component;
        }

        @Override
        public @NotNull String getName() {
            return "Agent Navigator";
        }

        @Override
        public VirtualFile getFile() {
            return file;
        }

        @Override
        public void setState(@NotNull FileEditorState state) {
            // nothing to restore
        }

        @Override
        public boolean isModified() {
            return false;
        }

        @Override
        public boolean isValid() {
            return true;
        }

        @Override
        public void addPropertyChangeListener(@NotNull PropertyChangeListener listener) {
            // never changes
        }

        @Override
        public void removePropertyChangeListener(@NotNull PropertyChangeListener listener) {
            // never changes
        }

        @Override
        public void dispose() {
            // the page is disposed with this editor (Disposer)
        }
    }
}
