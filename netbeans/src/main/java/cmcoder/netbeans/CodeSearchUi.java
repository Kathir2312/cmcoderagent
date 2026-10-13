package cmcoder.netbeans;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

import javax.swing.SwingUtilities;

import org.netbeans.api.progress.ProgressHandle;
import org.openide.NotifyDescriptor;
import org.openide.util.RequestProcessor;

import cmcoder.ide.core.CodeSearch;

/**
 * Code search in NetBeans (H16): the chat's toolbar button opens this menu;
 * the set-up asks what {@code cmcoder rag setup} asks, as dialogs. cmcoder does
 * every step; waiting happens in the background, never on the Swing thread.
 */
final class CodeSearchUi {
    private static final String TYPE_A_NAME = "Type a model name…";
    private static final RequestProcessor RP = new RequestProcessor(CodeSearchUi.class.getName(), 2);

    private CodeSearchUi() {}

    /** The toolbar button was clicked. Swing thread. */
    static void menu(CodeSearch search) {
        if (!search.setUp()) {
            setUp(search);
            return;
        }
        int answer = Dialogs.choose(Brand.product() + ": Code search", String.join("\n", search.lines()),
                NotifyDescriptor.PLAIN_MESSAGE, "Update the index", "Rebuild it", "Set up again", "Delete this project's index", "Close");
        switch (answer) {
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
                if (Dialogs.confirm("Delete this project's code index? It can be built again.")) index(search, "clear");
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
                Dialogs.warn("Couldn't list the models of " + provider + ": " + candidates.errors.get(provider));
            }
            String model = pickModel(candidates);
            if (model == null) return;

            int where = Dialogs.choose("Code search (2/4): where the index lives", "Where should the index of this project live?",
                    NotifyDescriptor.QUESTION_MESSAGE, "On this machine (built in)", "Chroma, by its URL (on this PC or a server)", "Cancel");
            if (where < 0 || where > 1) return;
            String store = new String[] {"local", "chroma-server"}[where];
            String url = null;
            String apiKey = null;
            boolean readOnly = false;
            if (store.equals("chroma-server")) {
                url = Dialogs.ask("Chroma's URL",
                        "http://localhost:8000 for Chroma on this PC, or a server's, e.g. https://chroma.example.com:8000", false);
                if (url == null) return;
                url = url.trim();
                if (!url.matches("^https?://\\S+$")) {
                    Dialogs.error("An http:// or https:// address, please.");
                    return;
                }
                apiKey = Dialogs.ask("Chroma API key", "Kept in your OS keychain, never in a file. Leave empty if it needs none.", true);
                if (apiKey == null) return;
                int mode = Dialogs.choose("Chroma: updates", "Should this project update the index in Chroma?",
                        NotifyDescriptor.QUESTION_MESSAGE, "Search and update it", "Only search it (someone else keeps it up to date)", "Cancel");
                if (mode < 0 || mode > 1) return;
                readOnly = mode == 1;
            }
            int scope = Dialogs.choose("Code search (3/4): whose settings", "Save these settings for:", NotifyDescriptor.QUESTION_MESSAGE,
                    "Just me (~/.cmcoder/settings.json)", "This project (.cmcoder/settings.json, shared through git)", "Cancel");
            if (scope < 0 || scope > 1) return;
            boolean indexNow = false;
            if (!readOnly) {
                int now = Dialogs.choose("Code search (4/4)", "Index this project now?", NotifyDescriptor.QUESTION_MESSAGE,
                        "Index it now", "Later", "Cancel");
                if (now < 0 || now > 1) return;
                indexNow = now == 0;
            }
            wait("Setting up code search",
                    search.setUp(model, store, url, apiKey, scope == 0 ? "user" : "project", readOnly, indexNow), result -> {
                        if (result == null) notRunning();
                        else if (result.ok) Dialogs.info(result.message);
                        else Dialogs.error("Code search: " + result.message);
                    });
        });
    }

    private static String pickModel(CodeSearch.Candidates candidates) {
        List<String> items = new ArrayList<>(candidates.likely);
        items.addAll(candidates.other);
        items.add(TYPE_A_NAME);
        String model = Dialogs.pick("Code search (1/4): embedding model", "A model on your gateway that turns code into vectors:", items,
                m -> candidates.likely.contains(m) ? m + "  (embedding model)" : m);
        if (model == null) return null;
        if (!TYPE_A_NAME.equals(model)) return model;
        String typed = Dialogs.ask("Embedding model", "provider:model, e.g. corp:bge-m3", false);
        return typed == null || typed.isBlank() ? null : typed.trim();
    }

    private static void notRunning() {
        Dialogs.warn(Brand.product() + " isn't running: open its chat first.");
    }

    /** Waits for {@code future} in the background with a progress bar, then runs {@code then} on the Swing thread. */
    private static <T> void wait(String title, CompletableFuture<T> future, Consumer<T> then) {
        RP.post(() -> {
            ProgressHandle progress = ProgressHandle.createHandle(Brand.product() + ": " + title);
            progress.start();
            T value;
            try {
                value = future.get(31, TimeUnit.MINUTES);
            } catch (Exception e) {
                value = null;
            } finally {
                progress.finish();
            }
            T result = value;
            SwingUtilities.invokeLater(() -> then.accept(result));
        });
    }
}
