package cmcoder.ide.core;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * The part of cmcoder's protocol (JSON lines over stdin/stdout) an IDE side
 * reads and writes itself; every other event goes to the panel unchanged.
 *
 * <p>{@link #READS} lists each field read here and {@code ProtocolSchemaTest}
 * checks it, and every message built here, against the schema from {@code
 * cmcoder protocol-schema}, so a protocol change this side doesn't follow fails
 * the build.
 */
public final class Protocol {
    /** The protocol version this side speaks (system_init's protocol_version). */
    public static final int VERSION = 1;

    private Protocol() {}

    /** One field the IDE side reads: event type, field, JSON type. */
    public static final class Read {
        public final String event;
        public final String field;
        public final String jsonType;

        Read(String event, String field, String jsonType) {
            this.event = event;
            this.field = field;
            this.jsonType = jsonType;
        }
    }

    public static final List<Read> READS = Collections.unmodifiableList(Arrays.asList(
            new Read("system_init", "protocol_version", "integer"),
            new Read("system_init", "model", "string"),
            new Read("system_init", "session_id", "string"),
            new Read("model_changed", "model", "string"),
            new Read("permission_request", "request_id", "string"),
            new Read("permission_request", "change", "object"),
            new Read("ide_tool_request", "request_id", "string"),
            new Read("ide_tool_request", "name", "string"),
            new Read("ide_tool_request", "input", "object"),
            new Read("result", "subtype", "string"),
            new Read("rewind_points", "points", "array"),
            new Read("index_status", "set_up", "boolean"),
            new Read("index_status", "active", "boolean"),
            new Read("index_status", "files", "integer"),
            new Read("index_status", "chunks", "integer"),
            new Read("index_status", "updating", "boolean"),
            new Read("index_status", "read_only", "boolean"),
            new Read("index_status", "lines", "array"),
            new Read("index_progress", "done", "integer"),
            new Read("index_progress", "total", "integer"),
            new Read("index_progress", "chunks", "integer"),
            new Read("rag_candidates", "likely", "array"),
            new Read("rag_candidates", "other", "array"),
            new Read("rag_candidates", "errors", "object"),
            new Read("rag_setup_result", "ok", "boolean"),
            new Read("rag_setup_result", "message", "string")));

    // -- events -----------------------------------------------------------------

    /** An event from cmcoder: its type, its fields, and the line as received. */
    public static final class Event {
        public final String type;
        public final Map<String, Object> fields;
        /** The JSON text as cmcoder sent it (relayed to the panel unchanged). */
        public final String raw;

        public Event(String type, Map<String, Object> fields, String raw) {
            this.type = type;
            this.fields = fields;
            this.raw = raw;
        }

        public String string(String key) {
            return Json.string(fields, key);
        }

        public Map<String, Object> object(String key) {
            return Json.object(fields, key);
        }

        /** A line from cmcoder's stdout as an event, or null if it isn't one. */
        public static Event parse(String line) {
            Map<String, Object> o = Json.parseObject(line);
            String type = Json.string(o, "type");
            return type == null ? null : new Event(type, o, line);
        }
    }

    /** A proposed file change (permission_request.change), for the IDE's diff viewer. */
    public static final class FileChange {
        public final String path;
        /** null for a new file. */
        public final String before;
        public final String after;

        public FileChange(String path, String before, String after) {
            this.path = path;
            this.before = before;
            this.after = after;
        }

        public static FileChange of(Map<String, Object> change) {
            String path = Json.string(change, "path");
            String after = Json.string(change, "after");
            return path == null || after == null ? null : new FileChange(path, Json.string(change, "before"), after);
        }
    }

    /** An earlier message to rewind to (rewind_points). */
    public static final class RewindPoint {
        public final long turn;
        public final String text;
        public final long filesChanged;
        public final List<String> outsideFiles;

        public RewindPoint(long turn, String text, long filesChanged, List<String> outsideFiles) {
            this.turn = turn;
            this.text = text;
            this.filesChanged = filesChanged;
            this.outsideFiles = outsideFiles;
        }

        @SuppressWarnings("unchecked")
        public static List<RewindPoint> of(Event event) {
            List<RewindPoint> points = new ArrayList<>();
            for (Object o : Json.list(event.fields, "points")) {
                if (!(o instanceof Map)) continue;
                Map<String, Object> p = (Map<String, Object>) o;
                List<String> outside = new ArrayList<>();
                for (Object f : Json.list(p, "outside_files")) if (f instanceof String) outside.add((String) f);
                String text = Json.string(p, "text");
                points.add(new RewindPoint(Json.number(p, "turn", 0), text == null ? "" : text, Json.number(p, "files_changed", 0), outside));
            }
            return points;
        }
    }

    // -- messages to cmcoder ------------------------------------------------------

    private static Map<String, Object> message(String type) {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("type", type);
        return m;
    }

    /** A user message; {@code context} is the editor context (or null), as built by {@link EditorContext}. */
    public static String userMessage(String text, Map<String, Object> context) {
        Map<String, Object> m = message("user_message");
        m.put("text", text);
        m.put("context", context);
        return Json.write(m);
    }

    public static String interrupt() {
        return Json.write(message("interrupt"));
    }

    public static String shutdown() {
        return Json.write(message("shutdown"));
    }

    public static String permissionResponse(String requestId, boolean allow, boolean remember, String feedback) {
        Map<String, Object> m = message("permission_response");
        m.put("request_id", requestId);
        m.put("allow", allow);
        m.put("remember", remember);
        m.put("feedback", feedback == null || feedback.isEmpty() ? null : feedback);
        return Json.write(m);
    }

    public static String ideCapabilities(List<String> tools) {
        Map<String, Object> m = message("ide_capabilities");
        m.put("tools", tools);
        return Json.write(m);
    }

    public static String ideToolResult(String requestId, String content, boolean isError) {
        Map<String, Object> m = message("ide_tool_result");
        m.put("request_id", requestId);
        m.put("content", content);
        m.put("is_error", isError);
        return Json.write(m);
    }

    public static String setMode(String mode) {
        Map<String, Object> m = message("set_mode");
        m.put("mode", mode);
        return Json.write(m);
    }

    public static String setCritique(boolean enabled) {
        Map<String, Object> m = message("set_critique");
        m.put("enabled", enabled);
        m.put("save", true);
        return Json.write(m);
    }

    public static String stopSubagent(String id) {
        Map<String, Object> m = message("stop_subagent");
        m.put("id", id);
        return Json.write(m);
    }

    public static String listSessions() {
        return Json.write(message("list_sessions"));
    }

    public static String listCommands() {
        return Json.write(message("list_commands"));
    }

    public static String rewind(long turn, boolean code, boolean conversation, boolean outside) {
        Map<String, Object> m = message("rewind");
        m.put("turn", turn);
        m.put("code", code);
        m.put("conversation", conversation);
        m.put("outside", outside);
        return Json.write(m);
    }

    /** Code search: the gateways' models, to pick an embedding model (answer: rag_candidates). */
    public static String ragCandidates() {
        return Json.write(message("rag_candidates"));
    }

    /**
     * Code search set-up, as {@code cmcoder rag setup} (answer: rag_setup_result).
     * store: "local", "chroma" or "chroma-server"; scope: "user" or "project".
     * The API key goes to cmcoder only, which keeps it in the OS keychain.
     */
    public static String ragSetup(String model, String store, String url, String apiKey, String scope,
            boolean readOnly, boolean indexNow) {
        Map<String, Object> m = message("rag_setup");
        m.put("embedding_model", model);
        m.put("store", store);
        m.put("url", url);
        m.put("api_key", apiKey == null || apiKey.isEmpty() ? null : apiKey);
        m.put("scope", scope);
        m.put("read_only", readOnly);
        m.put("index_now", indexNow);
        return Json.write(m);
    }

    /** Code search: "status", "update", "rebuild" or "clear". */
    public static String index(String action) {
        Map<String, Object> m = message("index");
        m.put("action", action);
        return Json.write(m);
    }

    /** Every message builder with sample arguments (for the schema test). */
    public static List<String> samples() {
        Map<String, Object> context = EditorContext.of("/p/a.py", EditorContext.selection("/p/a.py", 3, 5, "x = 1"),
                Collections.singletonList(EditorContext.diagnostic("/p/a.py", 3, "error", "bad", "pyright")));
        return Arrays.asList(
                userMessage("hello", context),
                userMessage("hello", null),
                interrupt(),
                shutdown(),
                permissionResponse("r1", true, false, null),
                permissionResponse("r1", false, false, "use b.py"),
                ideCapabilities(Arrays.asList("getDiagnostics", "openFile")),
                ideToolResult("t1", "No problems.", false),
                setMode("acceptEdits"),
                setCritique(true),
                stopSubagent("s1"),
                listSessions(),
                listCommands(),
                rewind(2, true, true, false),
                index("update"),
                ragCandidates(),
                ragSetup("corp:bge-m3", "chroma-server", "https://chroma.example:8000", "k", "user", true, false),
                ragSetup("corp:bge-m3", "local", null, null, "project", false, true));
    }
}
