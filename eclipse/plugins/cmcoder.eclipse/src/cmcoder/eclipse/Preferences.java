package cmcoder.eclipse;

import org.eclipse.core.runtime.preferences.AbstractPreferenceInitializer;
import org.eclipse.jface.preference.BooleanFieldEditor;
import org.eclipse.jface.preference.ComboFieldEditor;
import org.eclipse.jface.preference.FieldEditorPreferencePage;
import org.eclipse.jface.preference.FileFieldEditor;
import org.eclipse.jface.preference.IPreferenceStore;
import org.eclipse.ui.IWorkbench;
import org.eclipse.ui.IWorkbenchPreferencePage;

/** Window → Preferences → cmcoder (host duties H17, H21). */
public final class Preferences extends FieldEditorPreferencePage implements IWorkbenchPreferencePage {
    public static final String PROGRAM = "program";
    public static final String PERMISSION_MODE = "permissionMode";
    public static final String AUTO_CONTEXT = "autoContext";
    public static final String DIFF_REVIEW = "diffReview";
    public static final String TRUST_PROJECT = "trustProject";

    public Preferences() {
        super(GRID);
    }

    @Override
    public void init(IWorkbench workbench) {
        setPreferenceStore(store());
        setDescription(Brand.product() + " runs the cmcoder program inside this plugin; nothing else needs installing.");
    }

    public static IPreferenceStore store() {
        return Activator.get().getPreferenceStore();
    }

    @Override
    protected void createFieldEditors() {
        FileFieldEditor program = new FileFieldEditor(PROGRAM,
                "cmcoder program (leave empty to use the one in the plugin):", true, getFieldEditorParent());
        addField(program);
        addField(new ComboFieldEditor(PERMISSION_MODE, "Permission mode at start:", new String[][] {
            {"cmcoder's default", ""},
            {"default: ask before edits and commands", "default"},
            {"acceptEdits: edits without asking", "acceptEdits"},
            {"auto: ask only for risky actions", "auto"},
            {"plan: read only, plan first", "plan"},
        }, getFieldEditorParent()));
        addField(new BooleanFieldEditor(AUTO_CONTEXT,
                "Send the open file, the selection and its problems with each message", getFieldEditorParent()));
        addField(new BooleanFieldEditor(DIFF_REVIEW,
                "Show proposed changes in the compare editor (Accept / Reject there)", getFieldEditorParent()));
        addField(new BooleanFieldEditor(TRUST_PROJECT,
                "Use the project's own .cmcoder settings (only for projects you trust)", getFieldEditorParent()));
    }

    /** Defaults: the plugin's program, the editor context and diff review on, project settings off. */
    public static final class Defaults extends AbstractPreferenceInitializer {
        @Override
        public void initializeDefaultPreferences() {
            IPreferenceStore s = store();
            s.setDefault(PROGRAM, "");
            s.setDefault(PERMISSION_MODE, "");
            s.setDefault(AUTO_CONTEXT, true);
            s.setDefault(DIFF_REVIEW, true);
            s.setDefault(TRUST_PROJECT, false);
        }
    }
}
