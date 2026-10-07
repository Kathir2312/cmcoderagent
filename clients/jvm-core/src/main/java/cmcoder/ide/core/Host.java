package cmcoder.ide.core;

import java.nio.file.Path;
import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * The IDE side of one chat panel, the same for every JVM IDE: it owns the
 * cmcoder process for a project and relays between it, the chat page, the
 * Agent Navigator page and the IDE (through {@link Ide}). The behaviour is the
 * VS Code extension's (chatView.ts, navigator.ts); the host duties it covers
 * are named H1-H24 in docs/phase6/PLAN.md.
 */
public final class Host {
    /** Only a session id (from session_list) can become an argument (H12). */
    static final Pattern SESSION_ID = Pattern.compile("^[A-Za-z0-9][A-Za-z0-9-]{0,63}$");
    /** Only these addresses are opened, in the system browser (H18). */
    static final Pattern WEB_LINK = Pattern.compile("^https?://\\S+$", Pattern.CASE_INSENSITIVE);
    static final Set<String> MODES = new HashSet<>(Arrays.asList("default", "acceptEdits", "plan", "bypassPermissions"));

    /** How to start cmcoder for this project. */
    public static final class Config {
        /** The user's program setting (null: the bundled one). */
        public String programSetting;
        /** The plugin's folder (holds bin/cmcoder/). */
        public Path pluginDir;
        /** The project folder; null when none is open. */
        public Path projectDir;
        /** The default permission mode (null: cmcoder's). */
        public String permissionMode;
        /** The project's own .cmcoder settings may be used (H21). */
        public boolean trustProject;
        /** eclipse, netbeans (H23). */
        public String client;
        /** The brand name for messages. */
        public String product = "cmcoder";
        /** Extra environment (tests; a null value removes a variable). */
        public Map<String, String> env = new HashMap<>();
    }

    private final Ide ide;
    private final Config config;
    private final Navigator navigator = new Navigator();
    // Written with the host locked, read without: a UI thread may ask while the
    // host (locked) waits for that UI thread.
    private volatile AgentProcess agent;
    private volatile String state = "exited";
    private final Set<String> openDiffs = new HashSet<>();

    private final List<java.util.function.Consumer<Protocol.Event>> listeners = new java.util.concurrent.CopyOnWriteArrayList<>();

    /**
     * Every event from cmcoder, after the panel has it (e.g. {@link CodeSearch}).
     * Called on cmcoder's reader thread with this host locked: don't wait there.
     */
    public void addListener(java.util.function.Consumer<Protocol.Event> listener) {
        listeners.add(listener);
    }

    public Host(Ide ide, Config config) {
        this.ide = ide;
        this.config = config;
    }

    // -- starting and stopping (H1-H4) -------------------------------------------

    /** Starts a conversation (extra cmcoder arguments, e.g. --resume=ID); stops the one running. */
    public synchronized void start(List<String> extraArgs) {
        stopAgent();
        if (config.projectDir == null) {
            setState("exited", "Open a project first: " + config.product + " works on the files of a project.");
            return;
        }
        Map<String, String> env = new HashMap<>(System.getenv());
        env.putAll(config.env);
        env.values().removeIf(v -> v == null);
        ProgramLocator.Found found = ProgramLocator.find(config.programSetting, config.pluginDir, config.projectDir, env);
        if (found.program == null) {
            setState("exited", found.problem);
            return;
        }
        ProgramLocator.prepare(found.program);
        List<String> args = new ArrayList<>();
        if (config.client != null) {
            args.add("--client");
            args.add(config.client);
        }
        if (config.permissionMode != null && MODES.contains(config.permissionMode)) {
            args.add("--permission-mode");
            args.add(config.permissionMode);
        }
        if (config.trustProject) args.add("--trust-project");
        args.addAll(extraArgs);
        ide.log("Starting " + found.program + " --protocol stdio " + String.join(" ", args) + " in " + config.projectDir);
        setState("starting", null);
        // The holder, not the process, goes to the callbacks: they read it once
        // they hold the lock, which this method keeps until it's set. (Read
        // earlier, a fast first line arrived as from "no process" and was lost.)
        AgentProcess[] self = new AgentProcess[1];
        self[0] = AgentProcess.start(found.program, args, config.projectDir, config.env, new AgentProcess.Listener() {
            @Override
            public void onEvent(Protocol.Event event) {
                Host.this.onAgentEvent(self, event);
            }

            @Override
            public void onLog(String line) {
                ide.log(line);
            }

            @Override
            public void onExit(Integer code, boolean expected, String error) {
                Host.this.onAgentExit(self, code, expected, error);
            }
        });
        // start() reports a failed start through onExit before returning: only
        // keep a process that is (or was) running.
        if (self[0].running()) agent = self[0];
    }

    /** Never waits for the host's lock (safe on a UI thread). */
    public boolean running() {
        AgentProcess a = agent;
        return a != null && a.running();
    }

    /** The project or IDE closes: stop cmcoder (H3). */
    public void dispose() {
        AgentProcess a;
        synchronized (this) {
            a = agent;
            agent = null;
            closeAllDiffs();
        }
        if (a != null) a.stop();
    }

    private void stopAgent() {
        AgentProcess a = agent;
        agent = null;
        closeAllDiffs();
        if (a != null) a.stop();
    }

    private synchronized void onAgentExit(AgentProcess[] holder, Integer code, boolean expected, String error) {
        // null: it couldn't start, reported from within start() itself.
        AgentProcess which = holder[0];
        if (which != agent && which != null) return; // an old process, already replaced
        if (which == agent) agent = null;
        closeAllDiffs();
        ide.log(config.product + " exited (code " + code + ")");
        if (!expected) {
            setState("exited", error != null ? error
                    : config.product + " stopped unexpectedly (exit code " + code + "). See the " + config.product + " log.");
        }
    }

    // -- events from cmcoder (H5, H6, H9, H8, H11) ----------------------------------

    private synchronized void onAgentEvent(AgentProcess[] holder, Protocol.Event event) {
        if (holder[0] == null || holder[0] != agent) return; // an old process
        // The panel sees every event first and in order; side effects come after.
        ide.toPanel("{\"kind\":\"event\",\"event\":" + event.raw + "}");
        navigator.event(event);
        for (java.util.function.Consumer<Protocol.Event> l : listeners) {
            try {
                l.accept(event);
            } catch (RuntimeException e) {
                ide.log("A listener failed on " + event.type + ": " + e);
            }
        }
        switch (event.type) {
            case "system_init": {
                long version = Json.number(event.fields, "protocol_version", -1);
                if (version != Protocol.VERSION) {
                    setState("exited", "This plugin and the cmcoder program it started don't match (protocol "
                            + version + ", the plugin speaks " + Protocol.VERSION
                            + "). Install the plugin file again, or clear the cmcoder program setting.");
                    stopAgent();
                    return;
                }
                setState("ready", null);
                send(Protocol.ideCapabilities(ide.ideTools()));
                updateContext();
                break;
            }
            case "permission_request": {
                Protocol.FileChange change = Protocol.FileChange.of(event.object("change"));
                String id = event.string("request_id");
                if (change != null && id != null) {
                    changes.put(id, change);
                    if (ide.diffReview()) openDiff(id, change);
                }
                break;
            }
            case "ide_tool_request": {
                String id = event.string("request_id");
                String name = event.string("name");
                Map<String, Object> input = event.object("input");
                if (id == null || name == null) break;
                AgentProcess current = agent;
                ide.runIdeTool(name, input == null ? Collections.emptyMap() : input, (content, isError) -> {
                    if (current != null) current.send(Protocol.ideToolResult(id, content, isError));
                });
                break;
            }
            case "result":
                closeAllDiffs(); // an interrupted turn leaves no open requests
                break;
            case "rewind_points": {
                List<Protocol.RewindPoint> points = Protocol.RewindPoint.of(event);
                ide.pickRewind(points, c -> send(Protocol.rewind(c.turn, c.code, c.conversation, c.outside)));
                break;
            }
            default:
                break;
        }
    }

    private final Map<String, Protocol.FileChange> changes = new HashMap<>();

    private void openDiff(String id, Protocol.FileChange change) {
        openDiffs.add(id);
        ide.openDiff(id, change);
    }

    private void closeAllDiffs() {
        for (String id : new ArrayList<>(openDiffs)) ide.closeDiff(id);
        openDiffs.clear();
        changes.clear();
    }

    // -- the chat page (H5, H10, H12, H13, H18) ------------------------------------

    /** A message (JSON text) from the chat page. Anything malformed is ignored. */
    public synchronized void onPanelMessage(String json) {
        Map<String, Object> m = Json.parseObject(json);
        String kind = Json.string(m, "kind");
        if (kind == null) return;
        switch (kind) {
            case "ready":
                // The page (re)loaded: start a conversation if none is running.
                if (agent == null) start(Collections.emptyList());
                else toPanel(stateMessage(state, null));
                updateContext();
                break;
            case "send": {
                String text = Json.string(m, "text");
                List<Map<String, Object>> images = Protocol.images(Json.list(m, "images"));
                if ((text != null && !text.isEmpty()) || !images.isEmpty()) {
                    sendText(text == null ? "" : text, Json.bool(m, "includeContext"), images);
                }
                break;
            }
            case "pasteImage":
                // The page got no image from the clipboard (JavaFX's browser doesn't pass
                // them on): the IDE reads it, and the page shows it like a pasted one.
                ide.clipboardImage(png -> {
                    if (png != null) toPanel(Protocol.panelImage(png));
                });
                break;
            case "interrupt":
                send(Protocol.interrupt());
                break;
            case "stopSubagent": {
                String id = Json.string(m, "id");
                if (id != null) send(Protocol.stopSubagent(id));
                break;
            }
            case "openNavigator":
                ide.openNavigator();
                break;
            case "permission": {
                String id = Json.string(m, "requestId");
                if (id != null) answer(id, Json.bool(m, "allow"), Json.bool(m, "remember"), Json.string(m, "feedback"));
                break;
            }
            case "showDiff": {
                String id = Json.string(m, "requestId");
                Protocol.FileChange change = id == null ? null : changes.get(id);
                if (change != null) openDiff(id, change);
                break;
            }
            case "setMode": {
                String mode = Json.string(m, "mode");
                if (mode != null && MODES.contains(mode)) send(Protocol.setMode(mode));
                break;
            }
            case "setCritique":
                send(Protocol.setCritique(Json.bool(m, "enabled")));
                break;
            case "newConversation":
                newConversation(Collections.emptyList());
                break;
            case "listSessions":
                send(Protocol.listSessions());
                break;
            case "listCommands":
                send(Protocol.listCommands());
                break;
            case "resume": {
                String id = Json.string(m, "id");
                if (id != null && SESSION_ID.matcher(id).matches()) newConversation(Collections.singletonList("--resume=" + id));
                break;
            }
            case "attachFile":
                ide.attachFile(path -> toPanel(Json.write(map("kind", "prefill", "text", "@" + path.replace('\\', '/') + " "))));
                break;
            case "restart":
                start(Collections.emptyList());
                break;
            case "openLink": {
                String href = Json.string(m, "href");
                if (href != null && WEB_LINK.matcher(href).matches()) ide.openExternal(href);
                break;
            }
            default:
                break;
        }
    }

    /** Sends a message, with the editor context when asked and allowed (H7). */
    public synchronized boolean sendText(String text, boolean includeContext) {
        return sendText(text, includeContext, Collections.emptyList());
    }

    /** A message with images the user attached ({@link Protocol#images}). */
    public synchronized boolean sendText(String text, boolean includeContext, List<Map<String, Object>> images) {
        Map<String, Object> context = includeContext && ide.autoContext() ? ide.editorContext() : null;
        if (send(Protocol.userMessage(text, context, images))) {
            navigator.startTurn(text.isEmpty() ? "(" + images.size() + (images.size() == 1 ? " image)" : " images)") : text);
            return true;
        }
        setState("exited", config.product + " isn't running. Click Restart.");
        return false;
    }

    /** Answers a permission request, from the panel or the IDE's diff viewer (H10). */
    public synchronized void answer(String requestId, boolean allow, boolean remember, String feedback) {
        send(Protocol.permissionResponse(requestId, allow, remember, feedback));
        changes.remove(requestId);
        if (openDiffs.remove(requestId)) ide.closeDiff(requestId);
        toPanel(Json.write(map("kind", "permissionAnswered", "requestId", requestId,
                "text", allow ? (remember ? "Allowed (always)" : "Allowed") : "Denied")));
    }

    public synchronized void newConversation(List<String> extraArgs) {
        stopAgent();
        toPanel("{\"kind\":\"reset\"}");
        navigator.reset();
        start(extraArgs);
    }

    public synchronized void interrupt() {
        send(Protocol.interrupt());
    }

    /** "Ask about selection": the context, then a prompt in the input box (H14). */
    public synchronized void askAboutSelection() {
        ide.focusChat();
        updateContext();
        toPanel(Json.write(map("kind", "prefill", "text", "Explain the selected code. ")));
    }

    /** The editor's file, selection or problems changed: refresh the chat's context label (H7). */
    public synchronized void updateContext() {
        String label = ide.autoContext() ? EditorContext.label(ide.editorContext()) : null;
        toPanel(Json.write(map("kind", "context", "label", label)));
    }

    /**
     * Any protocol message (code search, tests); false when cmcoder isn't running.
     * Never waits for the host's lock (the process has its own): a UI thread may
     * send (code search's dialogs) while the host waits for that UI thread.
     */
    public boolean send(String json) {
        AgentProcess a = agent;
        return a != null && a.send(json);
    }

    /** Never waits for the host's lock (safe on a UI thread). */
    public String state() {
        return state;
    }

    private void setState(String s, String message) {
        state = s;
        if (message != null) ide.log(config.product + ": " + message);
        toPanel(stateMessage(s, message));
    }

    private static String stateMessage(String s, String message) {
        Map<String, Object> m = map("kind", "state", "state", s);
        if (message != null) m.put("message", message);
        return Json.write(m);
    }

    private void toPanel(String json) {
        ide.toPanel(json);
    }

    static Map<String, Object> map(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put((String) kv[i], kv[i + 1]);
        return m;
    }

    // -- the Agent Navigator (H15) --------------------------------------------------

    /** A message from the navigator page. */
    public synchronized void onNavigatorMessage(String json) {
        String kind = Json.string(Json.parseObject(json), "kind");
        if ("ready".equals(kind)) {
            navigator.replay();
        } else if ("stopSubagent".equals(kind)) {
            String id = Json.string(Json.parseObject(json), "id");
            if (id != null) send(Protocol.stopSubagent(id));
        } else if ("openChat".equals(kind)) {
            ide.focusChat();
        }
    }

    /** Keeps the current turn's map events, so the navigator opened mid-turn shows everything so far. */
    private final class Navigator {
        private final Set<String> mapEvents = new HashSet<>(Arrays.asList(
                "subagent_status", "tool_use", "tool_result", "permission_denied", "result"));
        private static final int MAX_EVENTS = 5000;
        private final ArrayDeque<String> turn = new ArrayDeque<>();
        private String prompt = "";
        private String model = "";
        private boolean busy;

        void startTurn(String text) {
            turn.clear();
            prompt = text;
            busy = true;
            post(resetMessage());
        }

        void reset() {
            turn.clear();
            prompt = "";
            busy = false;
            post(resetMessage());
        }

        void event(Protocol.Event event) {
            if (event.type.equals("system_init") || event.type.equals("model_changed")) {
                String m = event.string("model");
                if (m != null) model = m;
            }
            if (!mapEvents.contains(event.type)) return;
            if (event.type.equals("result")) busy = false;
            String message = "{\"kind\":\"event\",\"event\":" + event.raw + "}";
            turn.addLast(message);
            while (turn.size() > MAX_EVENTS) turn.removeFirst();
            post(message);
        }

        void replay() {
            post(resetMessage());
            for (String message : turn) post(message);
        }

        private String resetMessage() {
            return Json.write(map("kind", "reset", "prompt", prompt, "model", model, "busy", busy));
        }

        private void post(String json) {
            ide.toNavigator(json);
        }
    }
}
