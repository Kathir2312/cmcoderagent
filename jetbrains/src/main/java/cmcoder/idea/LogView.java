package cmcoder.idea;

import java.awt.Font;

import javax.swing.JComponent;

import com.intellij.openapi.Disposable;
import com.intellij.openapi.actionSystem.ActionManager;
import com.intellij.openapi.actionSystem.ActionToolbar;
import com.intellij.openapi.actionSystem.AnAction;
import com.intellij.openapi.actionSystem.DefaultActionGroup;
import com.intellij.openapi.application.ApplicationManager;
import com.intellij.openapi.ui.SimpleToolWindowPanel;
import com.intellij.ui.components.JBScrollPane;
import com.intellij.ui.components.JBTextArea;
import com.intellij.util.ui.JBUI;

/**
 * The tool window's Log tab (H22): cmcoder's stderr, starts and ends for this
 * project, live; Copy Diagnostics in its toolbar.
 */
final class LogView implements Disposable {
    /** The tab's name, also used to find it. */
    static final String TAB = "Log";
    private static final int MAX_CHARS = 200_000;

    private final JBTextArea text = new JBTextArea();
    private final SimpleToolWindowPanel component = new SimpleToolWindowPanel(true, true);
    private final Runnable stop;

    LogView(Session session) {
        text.setEditable(false);
        text.setFont(new Font(Font.MONOSPACED, Font.PLAIN, JBUI.Fonts.label().getSize()));
        for (String line : session.recentLog()) append(line);
        stop = session.onLog(line -> ApplicationManager.getApplication().invokeLater(() -> append(line)));

        DefaultActionGroup actions = new DefaultActionGroup();
        for (String id : new String[] {"cmcoder.CopyDiagnostics", "cmcoder.Settings"}) {
            AnAction action = ActionManager.getInstance().getAction(id);
            if (action != null) actions.add(action);
        }
        ActionToolbar toolbar = ActionManager.getInstance().createActionToolbar("cmcoderLog", actions, true);
        toolbar.setTargetComponent(component);
        component.setToolbar(toolbar.getComponent());
        component.setContent(new JBScrollPane(text));
    }

    JComponent component() {
        return component;
    }

    /** UI thread. */
    private void append(String line) {
        text.append(line + "\n");
        int excess = text.getDocument().getLength() - MAX_CHARS;
        if (excess > 0) text.replaceRange("", 0, excess);
        text.setCaretPosition(text.getDocument().getLength());
    }

    @Override
    public void dispose() {
        stop.run();
    }
}
