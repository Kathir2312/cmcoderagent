package cmcoder.netbeans;

import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.beans.PropertyChangeListener;
import java.beans.PropertyChangeSupport;
import java.util.prefs.Preferences;

import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import javax.swing.JComponent;
import javax.swing.JFileChooser;
import javax.swing.JLabel;
import javax.swing.JPanel;
import javax.swing.JTextField;

import org.netbeans.spi.options.OptionsPanelController;
import org.openide.util.HelpCtx;
import org.openide.util.Lookup;
import org.openide.util.NbPreferences;

/**
 * The settings (H17, H21): Tools → Options → Miscellaneous → cmcoder. They are
 * the user's own (NetBeans' user folder), never a project's.
 */
@OptionsPanelController.SubRegistration(location = "Advanced", id = "cmcoder", displayName = "cmcoder",
        keywords = "cmcoder AI assistant chat", keywordsCategory = "Advanced/cmcoder")
public final class Options extends OptionsPanelController {
    static final String PROGRAM = "program";
    static final String PERMISSION_MODE = "permissionMode";
    static final String AUTO_CONTEXT = "autoContext";
    static final String DIFF_REVIEW = "diffReview";
    static final String TRUST_PROJECT = "trustProject";

    private static final String[] MODES = {"", "default", "acceptEdits", "plan"};
    private static final String[] MODE_LABELS = {"cmcoder's default", "default: ask before edits and commands",
        "acceptEdits: edits without asking", "plan: read only, plan first"};

    static Preferences store() {
        return NbPreferences.forModule(Options.class);
    }

    /** The cmcoder program the user chose, or null for the one in the plugin. */
    static String program() {
        String p = store().get(PROGRAM, "").trim();
        return p.isEmpty() ? null : p;
    }

    static String permissionMode() {
        String m = store().get(PERMISSION_MODE, "");
        return m.isEmpty() ? null : m;
    }

    static boolean autoContext() {
        return store().getBoolean(AUTO_CONTEXT, true);
    }

    static boolean diffReview() {
        return store().getBoolean(DIFF_REVIEW, true);
    }

    static boolean trustProject() {
        return store().getBoolean(TRUST_PROJECT, false);
    }

    // -- the panel ---------------------------------------------------------------------

    private final PropertyChangeSupport changes = new PropertyChangeSupport(this);
    private JPanel panel;
    private JTextField program;
    private JComboBox<String> mode;
    private JCheckBox autoContext;
    private JCheckBox diffReview;
    private JCheckBox trustProject;

    @Override
    public JComponent getComponent(Lookup masterLookup) {
        if (panel == null) build();
        return panel;
    }

    private void build() {
        panel = new JPanel(new GridBagLayout());
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(4, 4, 4, 4);
        c.anchor = GridBagConstraints.WEST;
        c.gridy = 0;
        c.gridx = 0;
        panel.add(new JLabel(Brand.product() + " program (leave empty to use the one in the plugin):"), c);
        program = new JTextField(30);
        c.gridx = 1;
        c.fill = GridBagConstraints.HORIZONTAL;
        c.weightx = 1;
        panel.add(program, c);
        JButton browse = new JButton("Browse…");
        browse.addActionListener(e -> {
            JFileChooser chooser = new JFileChooser();
            if (chooser.showOpenDialog(panel) == JFileChooser.APPROVE_OPTION) program.setText(chooser.getSelectedFile().getPath());
        });
        c.gridx = 2;
        c.fill = GridBagConstraints.NONE;
        c.weightx = 0;
        panel.add(browse, c);

        c.gridy++;
        c.gridx = 0;
        panel.add(new JLabel("Permission mode at start:"), c);
        mode = new JComboBox<>(MODE_LABELS);
        c.gridx = 1;
        panel.add(mode, c);

        c.gridx = 0;
        c.gridwidth = 3;
        autoContext = new JCheckBox("Send the open file, the selection and its problems with each message");
        c.gridy++;
        panel.add(autoContext, c);
        diffReview = new JCheckBox("Show proposed changes in the diff viewer (Accept / Reject there)");
        c.gridy++;
        panel.add(diffReview, c);
        trustProject = new JCheckBox("Use the project's own .cmcoder settings (only for projects you trust)");
        c.gridy++;
        panel.add(trustProject, c);
        c.gridy++;
        c.weighty = 1;
        panel.add(new JLabel(" "), c);
    }

    @Override
    public void update() {
        if (panel == null) build();
        program.setText(store().get(PROGRAM, ""));
        String m = store().get(PERMISSION_MODE, "");
        mode.setSelectedIndex(0);
        for (int i = 0; i < MODES.length; i++) if (MODES[i].equals(m)) mode.setSelectedIndex(i);
        autoContext.setSelected(autoContext());
        diffReview.setSelected(diffReview());
        trustProject.setSelected(trustProject());
    }

    @Override
    public void applyChanges() {
        if (panel == null) return;
        store().put(PROGRAM, program.getText().trim());
        store().put(PERMISSION_MODE, MODES[Math.max(0, mode.getSelectedIndex())]);
        store().putBoolean(AUTO_CONTEXT, autoContext.isSelected());
        store().putBoolean(DIFF_REVIEW, diffReview.isSelected());
        store().putBoolean(TRUST_PROJECT, trustProject.isSelected());
    }

    @Override
    public void cancel() {
        // nothing saved until applyChanges
    }

    @Override
    public boolean isValid() {
        return true;
    }

    @Override
    public boolean isChanged() {
        return true;
    }

    @Override
    public HelpCtx getHelpCtx() {
        return null;
    }

    @Override
    public void addPropertyChangeListener(PropertyChangeListener l) {
        changes.addPropertyChangeListener(l);
    }

    @Override
    public void removePropertyChangeListener(PropertyChangeListener l) {
        changes.removePropertyChangeListener(l);
    }
}
