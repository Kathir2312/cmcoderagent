package cmcoder.idea;

import java.awt.BorderLayout;
import java.nio.file.Files;
import java.nio.file.InvalidPathException;
import java.nio.file.Path;

import javax.swing.JButton;
import javax.swing.JComponent;
import javax.swing.JPanel;

import org.jetbrains.annotations.Nullable;

import com.intellij.openapi.fileChooser.FileChooser;
import com.intellij.openapi.fileChooser.FileChooserDescriptor;
import com.intellij.openapi.options.Configurable;
import com.intellij.openapi.options.ConfigurationException;
import com.intellij.openapi.ui.ComboBox;
import com.intellij.openapi.vfs.VirtualFile;
import com.intellij.ui.components.JBCheckBox;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBTextField;
import com.intellij.util.ui.FormBuilder;
import com.intellij.util.ui.JBUI;
import com.intellij.util.ui.UIUtil;

/**
 * The settings page (H17): Settings → Tools → cmcoder. The IDE's settings, never
 * a project's: a project can't choose which program runs. Whether cmcoder
 * reads a project's own .cmcoder settings follows the IDE's trust in the
 * project (H21), so there is no switch for it here.
 */
public final class SettingsPage implements Configurable {
    static final String[] MODES = {"", "default", "acceptEdits", "auto", "plan"};
    private static final String[] MODE_LABELS = {"cmcoder's default", "default: ask before edits and commands",
        "acceptEdits: edits without asking", "auto: ask only for risky actions", "plan: read only, plan first"};

    private JPanel panel;
    private JBTextField program;
    private ComboBox<String> mode;
    private JBCheckBox autoContext;
    private JBCheckBox diffReview;

    @Override
    public String getDisplayName() {
        return Brand.product();
    }

    @Override
    public @Nullable JComponent createComponent() {
        program = new JBTextField();
        program.getEmptyText().setText("The one in the plugin");
        JButton browse = new JButton("Browse…");
        browse.addActionListener(e -> {
            VirtualFile chosen = FileChooser.chooseFile(new FileChooserDescriptor(true, false, false, false, false, false), panel, null,
                    null);
            String path = Workspace.path(chosen);
            if (path != null) program.setText(path);
        });
        JPanel programRow = new JPanel(new BorderLayout(JBUI.scale(4), 0));
        programRow.add(program, BorderLayout.CENTER);
        programRow.add(browse, BorderLayout.EAST);

        String[] labels = MODE_LABELS.clone();
        labels[0] = Brand.product() + "'s default";
        mode = new ComboBox<>(labels);
        autoContext = new JBCheckBox("Send the open file, the selection and its problems with each message");
        diffReview = new JBCheckBox("Show proposed changes in the diff viewer (Accept / Reject there)");
        JBLabel trust = new JBLabel("A project's own .cmcoder settings are used only when this IDE trusts the project.");
        trust.setComponentStyle(UIUtil.ComponentStyle.SMALL);
        trust.setForeground(JBUI.CurrentTheme.ContextHelp.FOREGROUND);
        JBLabel restart = new JBLabel("Changes apply the next time " + Brand.product() + " starts (New Conversation, or reopen the chat).");
        restart.setComponentStyle(UIUtil.ComponentStyle.SMALL);
        restart.setForeground(JBUI.CurrentTheme.ContextHelp.FOREGROUND);

        panel = FormBuilder.createFormBuilder()
                .addLabeledComponent(Brand.product() + " program:", programRow)
                .addTooltip("Leave empty to use the one in the plugin (or a newer shared copy); otherwise its full path.")
                .addLabeledComponent("Permission mode at start:", mode)
                .addComponent(autoContext)
                .addComponent(diffReview)
                .addComponent(trust)
                .addComponent(restart)
                .addComponentFillVertically(new JPanel(), 0)
                .getPanel();
        reset();
        return panel;
    }

    @Override
    public boolean isModified() {
        if (panel == null) return false;
        Settings.Values s = Settings.values();
        return !program.getText().trim().equals(s.program == null ? "" : s.program.trim())
                || !selectedMode().equals(s.permissionMode == null ? "" : s.permissionMode)
                || autoContext.isSelected() != s.autoContext
                || diffReview.isSelected() != s.diffReview;
    }

    @Override
    public void apply() throws ConfigurationException {
        if (panel == null) return;
        String path = program.getText().trim();
        if (!path.isEmpty()) {
            Path p;
            try {
                p = Path.of(path);
            } catch (InvalidPathException e) {
                throw new ConfigurationException("The program's path isn't valid: " + path);
            }
            if (!p.isAbsolute() || !Files.isRegularFile(p)) {
                throw new ConfigurationException("The program must be a file, by its full path (or leave it empty): " + path);
            }
        }
        Settings.Values s = Settings.values();
        s.program = path;
        s.permissionMode = selectedMode();
        s.autoContext = autoContext.isSelected();
        s.diffReview = diffReview.isSelected();
    }

    @Override
    public void reset() {
        if (panel == null) return;
        Settings.Values s = Settings.values();
        program.setText(s.program == null ? "" : s.program);
        mode.setSelectedIndex(0);
        for (int i = 0; i < MODES.length; i++) if (MODES[i].equals(s.permissionMode)) mode.setSelectedIndex(i);
        autoContext.setSelected(s.autoContext);
        diffReview.setSelected(s.diffReview);
    }

    @Override
    public void disposeUIResources() {
        panel = null;
        program = null;
        mode = null;
        autoContext = null;
        diffReview = null;
    }

    private String selectedMode() {
        return MODES[Math.max(0, mode.getSelectedIndex())];
    }
}
