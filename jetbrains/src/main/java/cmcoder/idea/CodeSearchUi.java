package cmcoder.idea;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.function.Consumer;
import java.util.function.Function;

import javax.swing.JComponent;
import javax.swing.JList;
import javax.swing.JPasswordField;
import javax.swing.JTextField;

import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import com.intellij.openapi.progress.ProgressIndicator;
import com.intellij.openapi.progress.ProgressManager;
import com.intellij.openapi.progress.Task;
import com.intellij.openapi.project.Project;
import com.intellij.openapi.ui.ComboBox;
import com.intellij.openapi.ui.DialogWrapper;
import com.intellij.openapi.ui.Messages;
import com.intellij.openapi.ui.ValidationInfo;
import com.intellij.ui.CollectionComboBoxModel;
import com.intellij.ui.SimpleListCellRenderer;
import com.intellij.ui.components.JBLabel;
import com.intellij.ui.components.JBPasswordField;
import com.intellij.ui.components.JBTextField;
import com.intellij.util.ui.FormBuilder;

import cmcoder.ide.core.CodeSearch;

/**
 * Code search in IntelliJ (H16): the status bar item opens this menu; the
 * set-up asks what {@code cmcoder rag setup} asks, as dialogs (as in Eclipse
 * and NetBeans). cmcoder does every step; waiting happens in the background
 * with a progress bar, never on the UI thread. UI thread.
 */
final class CodeSearchUi {
    private CodeSearchUi() {}

    static void menu(Project project, CodeSearch search) {
        if (!search.setUp()) {
            setUp(project, search);
            return;
        }
        int answer = Messages.showDialog(project, String.join("\n", search.lines()), Brand.product() + ": Code search",
                new String[] {"Update the index", "Rebuild it", "Set up again", "Delete this project's index", "Close"}, 0,
                Messages.getInformationIcon());
        switch (answer) {
            case 0:
                index(project, search, "update");
                break;
            case 1:
                index(project, search, "rebuild");
                break;
            case 2:
                setUp(project, search);
                break;
            case 3:
                if (Messages.showYesNoDialog(project, "Delete this project's code index? It can be built again.",
                        Brand.product() + ": Code search", Messages.getQuestionIcon()) == Messages.YES) {
                    index(project, search, "clear");
                }
                break;
            default:
                break;
        }
    }

    static void index(Project project, CodeSearch search, String action) {
        wait(project, "Code index", search.index(action), status -> {
            if (status == null) notRunning(project);
        });
    }

    /** Embedding model, where the index lives, whose settings, index now: as {@code cmcoder rag setup}. */
    static void setUp(Project project, CodeSearch search) {
        wait(project, "Asking the gateway for its models", search.candidates(), candidates -> {
            if (candidates == null) {
                notRunning(project);
                return;
            }
            if (!candidates.errors.isEmpty()) {
                List<String> lines = new ArrayList<>();
                for (Map.Entry<String, String> e : candidates.errors.entrySet()) {
                    lines.add("Couldn't list the models of " + e.getKey() + ": " + e.getValue());
                }
                Messages.showWarningDialog(project, String.join("\n", lines), Brand.product() + ": Code search");
            }
            String model = ModelDialog.pick(project, candidates);
            if (model == null) return;

            int where = Messages.showDialog(project, "Where should the index of this project live?",
                    "Code search (2/4): where the index lives",
                    new String[] {"On this machine (built in)", "Chroma, by its URL (on this PC or a server)", "Cancel"}, 0,
                    Messages.getQuestionIcon());
            if (where < 0 || where > 1) return;
            String store = new String[] {"local", "chroma-server"}[where];
            String url = null;
            String apiKey = null;
            boolean readOnly = false;
            if (store.equals("chroma-server")) {
                url = AskDialog.ask(project, "Chroma's URL",
                        "http://localhost:8000 for Chroma on this PC, or a server's, e.g. https://chroma.example.com:8000", false,
                        v -> v.trim().matches("^https?://\\S+$") ? null : "An http:// or https:// address, please.");
                if (url == null) return;
                url = url.trim();
                apiKey = AskDialog.ask(project, "Chroma API key",
                        "Kept in your OS keychain, never in a file. Leave empty if it needs none.", true, null);
                if (apiKey == null) return;
                int mode = Messages.showDialog(project, "Should this project update the index in Chroma?", "Chroma: updates",
                        new String[] {"Search and update it", "Only search it (someone else keeps it up to date)", "Cancel"}, 0,
                        Messages.getQuestionIcon());
                if (mode < 0 || mode > 1) return;
                readOnly = mode == 1;
            }
            int scope = Messages.showDialog(project, "Save these settings for:", "Code search (3/4): whose settings",
                    new String[] {"Just me (~/.cmcoder/settings.json)", "This project (.cmcoder/settings.json, shared through git)",
                        "Cancel"},
                    0, Messages.getQuestionIcon());
            if (scope < 0 || scope > 1) return;
            boolean indexNow = false;
            if (!readOnly) {
                int now = Messages.showDialog(project, "Index this project now?", "Code search (4/4)",
                        new String[] {"Index it now", "Later", "Cancel"}, 0, Messages.getQuestionIcon());
                if (now < 0 || now > 1) return;
                indexNow = now == 0;
            }
            wait(project, "Setting up code search",
                    search.setUp(model, store, url, apiKey, scope == 0 ? "user" : "project", readOnly, indexNow), result -> {
                        if (result == null) notRunning(project);
                        else if (result.ok) Messages.showInfoMessage(project, result.message, Brand.product() + ": Code search");
                        else Messages.showErrorDialog(project, "Code search: " + result.message, Brand.product());
                    });
        });
    }

    private static void notRunning(Project project) {
        Messages.showWarningDialog(project, Brand.product() + " isn't running: open its chat first.", Brand.product());
    }

    /**
     * Waits for {@code future} in the background with a progress bar (which can
     * stop the waiting, not cmcoder's work), then runs {@code then} on the UI thread.
     */
    private static <T> void wait(Project project, String title, CompletableFuture<T> future, Consumer<T> then) {
        ProgressManager.getInstance().run(new Task.Backgroundable(project, Brand.product() + ": " + title, true) {
            private T value;

            @Override
            public void run(@NotNull ProgressIndicator indicator) {
                indicator.setIndeterminate(true);
                long deadline = System.nanoTime() + TimeUnit.MINUTES.toNanos(31);
                while (System.nanoTime() < deadline) {
                    indicator.checkCanceled();
                    try {
                        value = future.get(250, TimeUnit.MILLISECONDS);
                        return;
                    } catch (TimeoutException e) {
                        // still waiting
                    } catch (ExecutionException e) {
                        return;
                    } catch (InterruptedException e) {
                        Thread.currentThread().interrupt();
                        return;
                    }
                }
            }

            @Override
            public void onSuccess() {
                if (!project.isDisposed()) then.accept(value);
            }
        });
    }

    /** A line of text (or a secret, not shown), with an optional check: the problem, or null when it's fine. */
    static final class AskDialog extends DialogWrapper {
        private final String message;
        private final JTextField field;
        private final Function<String, String> check;

        private AskDialog(Project project, String title, String message, boolean secret, Function<String, String> check) {
            super(project, false);
            this.message = message;
            this.check = check;
            field = secret ? new JBPasswordField() : new JBTextField();
            field.setColumns(40);
            setTitle(title);
            init();
        }

        static String ask(Project project, String title, String message, boolean secret, Function<String, String> check) {
            AskDialog dialog = new AskDialog(project, title, message, secret, check);
            return dialog.showAndGet() ? dialog.value() : null;
        }

        private String value() {
            return field instanceof JPasswordField ? new String(((JPasswordField) field).getPassword()) : field.getText();
        }

        @Override
        protected @Nullable JComponent createCenterPanel() {
            return FormBuilder.createFormBuilder().addComponent(new JBLabel(message)).addComponent(field).getPanel();
        }

        @Override
        public @Nullable JComponent getPreferredFocusedComponent() {
            return field;
        }

        @Override
        protected @Nullable ValidationInfo doValidate() {
            String problem = check == null ? null : check.apply(value());
            return problem == null ? null : new ValidationInfo(problem, field);
        }
    }

    /** The embedding model: one of the gateways' (likely ones first and marked), or a name typed in. */
    static final class ModelDialog extends DialogWrapper {
        private final ComboBox<String> models;

        private ModelDialog(Project project, CodeSearch.Candidates candidates) {
            super(project, false);
            List<String> items = new ArrayList<>(candidates.likely);
            items.addAll(candidates.other);
            models = new ComboBox<>(new CollectionComboBoxModel<>(items));
            models.setEditable(true);
            models.setRenderer(new SimpleListCellRenderer<String>() {
                @Override
                public void customize(@NotNull JList<? extends String> list, String value, int index, boolean selected,
                        boolean hasFocus) {
                    setText(value == null ? "" : candidates.likely.contains(value) ? value + "  (embedding model)" : value);
                }
            });
            if (!items.isEmpty()) models.setSelectedIndex(0);
            setTitle("Code search (1/4): embedding model");
            init();
        }

        static String pick(Project project, CodeSearch.Candidates candidates) {
            ModelDialog dialog = new ModelDialog(project, candidates);
            return dialog.showAndGet() ? dialog.value() : null;
        }

        private String value() {
            Object item = models.getEditor().getItem();
            return item == null ? "" : item.toString().trim();
        }

        @Override
        protected @Nullable JComponent createCenterPanel() {
            return FormBuilder.createFormBuilder()
                    .addComponent(new JBLabel("A model on your gateway that turns code into vectors."))
                    .addComponent(new JBLabel("Pick one, or type provider:model (e.g. corp:bge-m3)."))
                    .addComponent(models)
                    .getPanel();
        }

        @Override
        public @Nullable JComponent getPreferredFocusedComponent() {
            return models;
        }

        @Override
        protected @Nullable ValidationInfo doValidate() {
            return value().isEmpty() ? new ValidationInfo("Choose a model or type its name.", models) : null;
        }
    }
}
