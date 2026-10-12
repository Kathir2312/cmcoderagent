package cmcoder.idea;

import org.jetbrains.annotations.NotNull;

import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.components.PersistentStateComponent;
import com.intellij.openapi.components.Service;
import com.intellij.openapi.components.State;
import com.intellij.openapi.components.Storage;

/**
 * The plugin's settings (H17), for the IDE, never a project: a project can't
 * choose which program runs. The settings page is item 4; these are the values
 * and their defaults.
 */
@Service(Service.Level.APP)
@State(name = "cmcoder", storages = @Storage("cmcoder.xml"))
public final class Settings implements PersistentStateComponent<Settings.Values> {
    /** The stored values (public fields: IntelliJ serialises them). */
    public static final class Values {
        /** A full path to cmcoder; empty: the shared or bundled copy. */
        public String program = "";
        /** default, acceptEdits, auto, plan, bypassPermissions; empty: cmcoder's own setting. */
        public String permissionMode = "";
        /** Send the editor's file, selection and problems with each message (H7). */
        public boolean autoContext = true;
        /** Show proposed changes in the IDE's diff viewer (H9). */
        public boolean diffReview = true;
    }

    private Values values = new Values();

    public static Settings get() {
        return ApplicationManager.getApplication().getService(Settings.class);
    }

    public static Values values() {
        return get().values;
    }

    @Override
    public Values getState() {
        return values;
    }

    @Override
    public void loadState(@NotNull Values state) {
        values = state;
    }
}
