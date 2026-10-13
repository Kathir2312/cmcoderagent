package cmcoder.ide.core;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

/**
 * Code search for an IDE (H16), the same for every JVM IDE: the index's state
 * for a status item, and the requests behind its menu and set-up. As in VS
 * Code (codeSearch.ts), cmcoder does every step over the protocol, so the
 * terminal and the IDEs write the same settings and use the same index.
 */
public final class CodeSearch {
    /** Told when the status item should change (any thread). */
    public interface Listener {
        void changed(CodeSearch codeSearch);
    }

    private final Host host;
    private final Listener listener;
    private final Map<String, CompletableFuture<Protocol.Event>> waiting = new HashMap<>();
    private Map<String, Object> status;
    private String progress;

    public CodeSearch(Host host, Listener listener) {
        this.host = host;
        this.listener = listener;
        host.addListener(this::onEvent);
    }

    private void onEvent(Protocol.Event event) {
        CompletableFuture<Protocol.Event> done;
        synchronized (this) {
            switch (event.type) {
                case "system_init":
                    status = null;
                    progress = null;
                    host.send(Protocol.index("status")); // for the status item
                    break;
                case "index_status":
                    status = event.fields;
                    progress = null;
                    break;
                case "index_progress":
                    progress = Json.number(event.fields, "done", 0) + "/" + Json.number(event.fields, "total", 0) + " files";
                    break;
                default:
                    break;
            }
            done = waiting.remove(event.type);
        }
        if (event.type.equals("system_init") || event.type.startsWith("index_")) listener.changed(this);
        // Completed off this thread: whoever waits mustn't run with the host locked.
        if (done != null) CompletableFuture.runAsync(() -> done.complete(event));
    }

    /**
     * Sends a message and gives the next event of {@code type}: null when
     * cmcoder isn't running or nothing came within the time.
     */
    public CompletableFuture<Protocol.Event> request(String message, String type, long timeoutMs) {
        CompletableFuture<Protocol.Event> future = new CompletableFuture<>();
        CompletableFuture<Protocol.Event> previous;
        synchronized (this) {
            previous = waiting.put(type, future);
        }
        if (previous != null) previous.complete(null);
        if (!host.send(message)) {
            synchronized (this) {
                waiting.remove(type, future);
            }
            future.complete(null);
            return future;
        }
        return future.completeOnTimeout(null, timeoutMs, TimeUnit.MILLISECONDS);
    }

    /** The gateways' models: likely embedding models first. Null if it didn't answer. */
    public CompletableFuture<Candidates> candidates() {
        return request(Protocol.ragCandidates(), "rag_candidates", 60_000).thenApply(e -> e == null ? null : new Candidates(e));
    }

    /** The models to choose from, and the gateways that couldn't list theirs. */
    public static final class Candidates {
        public final List<String> likely = new ArrayList<>();
        public final List<String> other = new ArrayList<>();
        public final Map<String, String> errors = new HashMap<>();

        Candidates(Protocol.Event e) {
            strings(Json.list(e.fields, "likely"), likely);
            strings(Json.list(e.fields, "other"), other);
            Map<String, Object> errs = Json.object(e.fields, "errors");
            if (errs != null) errs.forEach((k, v) -> errors.put(k, String.valueOf(v)));
        }
    }

    /** Sets code search up (indexing can take long). Null if cmcoder isn't running. */
    public CompletableFuture<Result> setUp(String model, String store, String url, String apiKey, String scope,
            boolean readOnly, boolean indexNow) {
        return request(Protocol.ragSetup(model, store, url, apiKey, scope, readOnly, indexNow), "rag_setup_result",
                TimeUnit.MINUTES.toMillis(30)).thenApply(e -> e == null ? null
                        : new Result(Json.bool(e.fields, "ok"), Json.string(e.fields, "message")));
    }

    /** "update", "rebuild" or "clear"; the new state when done (null if cmcoder isn't running). */
    public CompletableFuture<Protocol.Event> index(String action) {
        return request(Protocol.index(action), "index_status", TimeUnit.MINUTES.toMillis(30));
    }

    public static final class Result {
        public final boolean ok;
        public final String message;

        Result(boolean ok, String message) {
            this.ok = ok;
            this.message = message == null ? "" : message;
        }
    }

    // -- the status item --------------------------------------------------------------

    /** Whether code search is set up (false before cmcoder said). */
    public synchronized boolean setUp() {
        return status != null && Json.bool(status, "set_up");
    }

    /** The status item's text; null: hide it (cmcoder hasn't said yet). */
    public synchronized String text() {
        if (progress != null) return "Indexing " + progress;
        if (status == null) return null;
        if (!Json.bool(status, "set_up")) return "Code search: off";
        String error = Json.string(status, "error");
        if (error != null && !Json.bool(status, "active")) return "Code search: problem";
        if (Json.bool(status, "updating")) return "Code search: updating";
        long files = Json.number(status, "files", 0);
        if (files == 0 && Json.number(status, "chunks", 0) == 0) return "Code search: not indexed";
        return String.format(Locale.ROOT, "Code search: %,d files", files);
    }

    /** The status item's tooltip. */
    public synchronized String tooltip() {
        if (progress != null) return "Indexing this project: " + progress;
        if (status == null) return "";
        if (!Json.bool(status, "set_up")) return "Click to set up code search (an index of this project for the model).";
        String error = Json.string(status, "error");
        if (error != null && !Json.bool(status, "active")) return error;
        long files = Json.number(status, "files", 0);
        if (files == 0 && Json.number(status, "chunks", 0) == 0) return "Click to index this project.";
        List<String> lines = new ArrayList<>();
        strings(Json.list(status, "lines"), lines);
        return String.join("\n", lines) + (error != null ? "\nLast update failed: " + error : "");
    }

    /** The index's description, one line per fact (for "Show the index"). */
    public synchronized List<String> lines() {
        List<String> lines = new ArrayList<>();
        if (status != null) strings(Json.list(status, "lines"), lines);
        return lines;
    }

    private static void strings(List<Object> from, List<String> to) {
        if (from == null) return;
        for (Object o : from) if (o instanceof String) to.add((String) o);
    }
}
