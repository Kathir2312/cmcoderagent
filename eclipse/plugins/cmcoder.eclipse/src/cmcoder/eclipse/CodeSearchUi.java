package cmcoder.eclipse;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import org.eclipse.core.runtime.IProgressMonitor;
import org.eclipse.core.runtime.IStatus;
import org.eclipse.core.runtime.Status;
import org.eclipse.core.runtime.jobs.Job;
import org.eclipse.jface.dialogs.IInputValidator;
import org.eclipse.jface.dialogs.InputDialog;
import org.eclipse.jface.dialogs.MessageDialog;
import org.eclipse.jface.viewers.LabelProvider;
import org.eclipse.jface.window.Window;
import org.eclipse.swt.SWT;
import org.eclipse.swt.widgets.Display;
import org.eclipse.swt.widgets.Shell;
import org.eclipse.ui.PlatformUI;
import org.eclipse.ui.dialogs.ElementListSelectionDialog;

import cmcoder.ide.core.CodeSearch;

/**
 * Code search in Eclipse (H16): the chat view's status item opens this menu;
 * the set-up asks what {@code cmcoder rag setup} asks, as dialogs. cmcoder does
 * every step; waiting happens in background jobs, never on the UI thread.
 */
final class CodeSearchUi {
    private static final String TYPE_A_NAME = "Type a model name…";

    private CodeSearchUi() {}

    private static Shell shell() {
        return PlatformUI.getWorkbench().getModalDialogShellProvider().getShell();
    }

    /** The status item was clicked. UI thread. */
    static void menu(CodeSearch search) {
        if (!search.setUp()) {
            setUp(search);
            return;
        }
        MessageDialog menu = new MessageDialog(shell(), Brand.product() + ": Code search", null,
                String.join("\n", search.lines()), MessageDialog.NONE, 0,
                "Update the index", "Rebuild it", "Set up again", "Delete this project's index", "Close");
        switch (menu.open()) {
            case 0:
                index(search, "update");
                break;
            case 1:
                index(search, "rebuild");
                break;
            case 2:
                setUp(search);
                break;
            case 3:
                if (MessageDialog.openConfirm(shell(), Brand.product(), "Delete this project's code index? It can be built again."))
                    index(search, "clear");
                break;
            default:
                break;
        }
    }

    static void index(CodeSearch search, String action) {
        wait("Code index", search.index(action), status -> {
            if (status == null) notRunning();
        });
    }

    /** Embedding model, where the index lives, whose settings, index now: as {@code cmcoder rag setup}. */
    static void setUp(CodeSearch search) {
        wait("Asking the gateway for its models", search.candidates(), candidates -> {
            if (candidates == null) {
                notRunning();
                return;
            }
            for (String provider : candidates.errors.keySet()) {
                MessageDialog.openWarning(shell(), Brand.product(),
                        "Couldn't list the models of " + provider + ": " + candidates.errors.get(provider));
            }
            String model = pickModel(candidates);
            if (model == null) return;

            int where = new MessageDialog(shell(), "Code search (2/4): where the index lives", null,
                    "Where should the index of this project live?", MessageDialog.QUESTION, 0,
                    "On this machine (built in)", "Chroma, by its URL (on this PC or a server)", "Cancel").open();
            if (where < 0 || where > 1) return;
            String store = new String[] {"local", "chroma-server"}[where];
            String url = null;
            String apiKey = null;
            boolean readOnly = false;
            if (store.equals("chroma-server")) {
                url = ask("Chroma's URL",
                        "http://localhost:8000 for Chroma on this PC, or a server's, e.g. https://chroma.example.com:8000", false,
                        v -> v.trim().matches("^https?://\\S+$") ? null : "An http:// or https:// address");
                if (url == null) return;
                url = url.trim();
                apiKey = ask("Chroma API key",
                        "Kept in your OS keychain, never in a file. Leave empty if it needs none.", true, null);
                if (apiKey == null) return;
                int mode = new MessageDialog(shell(), "Chroma: updates", null,
                        "Should this project update the index in Chroma?", MessageDialog.QUESTION, 0,
                        "Search and update it", "Only search it (someone else keeps it up to date)", "Cancel").open();
                if (mode < 0 || mode > 1) return;
                readOnly = mode == 1;
            }
            int scope = new MessageDialog(shell(), "Code search (3/4): whose settings", null,
                    "Save these settings for:", MessageDialog.QUESTION, 0,
                    "Just me (~/.cmcoder/settings.json)", "This project (.cmcoder/settings.json, shared through git)",
                    "Cancel").open();
            if (scope < 0 || scope > 1) return;
            boolean indexNow = false;
            if (!readOnly) {
                int now = new MessageDialog(shell(), "Code search (4/4)", null, "Index this project now?",
                        MessageDialog.QUESTION, 0, "Index it now", "Later", "Cancel").open();
                if (now < 0 || now > 1) return;
                indexNow = now == 0;
            }
            wait("Setting up code search",
                    search.setUp(model, store, url, apiKey, scope == 0 ? "user" : "project", readOnly, indexNow), result -> {
                        if (result == null) notRunning();
                        else if (result.ok) MessageDialog.openInformation(shell(), Brand.product(), result.message);
                        else MessageDialog.openError(shell(), Brand.product(), "Code search: " + result.message);
                    });
        });
    }

    private static String pickModel(CodeSearch.Candidates candidates) {
        List<String> items = new ArrayList<>(candidates.likely);
        items.addAll(candidates.other);
        items.add(TYPE_A_NAME);
        ElementListSelectionDialog dialog = new ElementListSelectionDialog(shell(), new LabelProvider() {
            @Override
            public String getText(Object element) {
                return candidates.likely.contains(element) ? element + "  (embedding model)" : String.valueOf(element);
            }
        });
        dialog.setTitle("Code search (1/4): embedding model");
        dialog.setMessage("A model on your gateway that turns code into vectors:");
        dialog.setElements(items.toArray());
        dialog.setMultipleSelection(false);
        if (dialog.open() != Window.OK || dialog.getFirstResult() == null) return null;
        String model = (String) dialog.getFirstResult();
        if (!TYPE_A_NAME.equals(model)) return model;
        String typed = ask("Embedding model", "provider:model, e.g. corp:bge-m3", false,
                v -> v.trim().isEmpty() ? "A model name" : null);
        return typed == null ? null : typed.trim();
    }

    /** A line of text, or null if cancelled; {@code secret} hides what's typed. */
    private static String ask(String title, String prompt, boolean secret, IInputValidator validator) {
        InputDialog dialog = new InputDialog(shell(), title, prompt, "", validator) {
            @Override
            protected int getInputTextStyle() {
                return secret ? SWT.SINGLE | SWT.BORDER | SWT.PASSWORD : super.getInputTextStyle();
            }
        };
        return dialog.open() == Window.OK ? dialog.getValue() : null;
    }

    private static void notRunning() {
        MessageDialog.openWarning(shell(), Brand.product(), Brand.product() + " isn't running: open its chat first.");
    }

    /** Waits for {@code future} in a background job, then runs {@code then} on the UI thread. */
    private static <T> void wait(String title, CompletableFuture<T> future, Consumer<T> then) {
        Job job = new Job(Brand.product() + ": " + title) {
            @Override
            protected IStatus run(IProgressMonitor monitor) {
                T value;
                try {
                    value = future.get(31, TimeUnit.MINUTES);
                } catch (Exception e) {
                    value = null;
                }
                T result = value;
                Display display = PlatformUI.getWorkbench().getDisplay();
                if (!display.isDisposed()) display.asyncExec(() -> then.accept(result));
                return Status.OK_STATUS;
            }
        };
        job.setUser(true);
        job.schedule();
    }
}
