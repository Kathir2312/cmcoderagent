using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;

namespace Cmcoder.Core
{
    /// <summary>
    /// The IDE side of one chat panel: it owns the cmcoder process for a project
    /// and relays between it, the chat page, the Agent Navigator page and the
    /// IDE (through <see cref="IIde"/>). A port of the JVM core's Host (the VS
    /// Code extension's chatView.ts and navigator.ts); the host duties are
    /// H1-H24 in docs/phase6/PLAN.md.
    /// </summary>
    public sealed class Host
    {
        /// <summary>Only a session id (from session_list) can become an argument (H12).</summary>
        internal static readonly Regex SessionId = new Regex("^[A-Za-z0-9][A-Za-z0-9-]{0,63}$");

        /// <summary>Only these addresses are opened, in the system browser (H18).</summary>
        internal static readonly Regex WebLink = new Regex(@"^https?://\S+$", RegexOptions.IgnoreCase);

        internal static readonly HashSet<string> Modes = new HashSet<string> { "default", "acceptEdits", "plan", "bypassPermissions" };

        /// <summary>How to start cmcoder for this project.</summary>
        public sealed class Config
        {
            /// <summary>The user's program setting (null: the bundled one).</summary>
            public string? ProgramSetting { get; set; }

            /// <summary>The extension's folder (holds bin/cmcoder/).</summary>
            public string? ExtensionDir { get; set; }

            /// <summary>The project folder; null when none is open.</summary>
            public string? ProjectDir { get; set; }

            /// <summary>The default permission mode (null: cmcoder's).</summary>
            public string? PermissionMode { get; set; }

            /// <summary>The project's own .cmcoder settings may be used (H21).</summary>
            public bool TrustProject { get; set; }

            /// <summary>visualstudio (H23).</summary>
            public string? Client { get; set; }

            /// <summary>The brand name for messages.</summary>
            public string Product { get; set; } = "cmcoder";

            /// <summary>Extra environment (tests; a null value removes a variable).</summary>
            public IDictionary<string, string?> Env { get; set; } = new Dictionary<string, string?>();
        }

        private readonly IIde ide;
        private readonly Config config;
        private readonly object gate = new object();
        private readonly Navigator navigator;
        private readonly HashSet<string> openDiffs = new HashSet<string>();
        private readonly Dictionary<string, Protocol.FileChange> changes = new Dictionary<string, Protocol.FileChange>();
        private readonly List<Action<Protocol.Event>> listeners = new List<Action<Protocol.Event>>();
        // Written under the lock, read without it: the IDE's UI thread may ask for the
        // state while the host (holding its lock) waits for that UI thread.
        private volatile AgentProcess? agent;
        private volatile string state = "exited";

        public Host(IIde ide, Config config)
        {
            this.ide = ide;
            this.config = config;
            navigator = new Navigator(ide);
        }

        /// <summary>
        /// Every event from cmcoder, after the panel has it (e.g. <see cref="CodeSearch"/>).
        /// Called on cmcoder's reader thread with this host locked: don't wait there.
        /// </summary>
        public void AddListener(Action<Protocol.Event> listener)
        {
            lock (listeners) listeners.Add(listener);
        }

        // -- starting and stopping (H1-H4) ------------------------------------------------

        /// <summary>Starts a conversation (extra cmcoder arguments, e.g. --resume=ID); stops the one running.</summary>
        public void Start(IList<string> extraArgs)
        {
            lock (gate)
            {
                StopAgent();
                if (config.ProjectDir == null)
                {
                    SetState("exited", "Open a solution or folder first: " + config.Product + " works on the files of a project.");
                    return;
                }
                var env = new Dictionary<string, string?>(StringComparer.OrdinalIgnoreCase);
                foreach (System.Collections.DictionaryEntry e in Environment.GetEnvironmentVariables())
                    env[(string)e.Key] = (string?)e.Value;
                foreach (var e in config.Env) env[e.Key] = e.Value;
                var found = ProgramLocator.Find(config.ProgramSetting, config.ExtensionDir, config.ProjectDir,
                    env.Where(e => e.Value != null).ToDictionary(e => e.Key, e => e.Value, StringComparer.OrdinalIgnoreCase));
                if (found.Program == null)
                {
                    SetState("exited", found.Problem);
                    return;
                }
                var args = new List<string>();
                if (config.Client != null)
                {
                    args.Add("--client");
                    args.Add(config.Client);
                }
                if (config.PermissionMode != null && Modes.Contains(config.PermissionMode))
                {
                    args.Add("--permission-mode");
                    args.Add(config.PermissionMode);
                }
                if (config.TrustProject) args.Add("--trust-project");
                args.AddRange(extraArgs);
                ide.Log("Starting " + found.Program + " --protocol stdio " + string.Join(" ", args) + " in " + config.ProjectDir);
                SetState("starting", null);
                // The holder, not the process, goes to the listener: it reads it once it
                // holds the lock, which this method keeps until it's set (a fast first
                // line would otherwise look like it came from no process).
                var holder = new AgentProcess?[1];
                holder[0] = AgentProcess.Start(found.Program, args, config.ProjectDir, config.Env, new Listener(this, holder));
                if (holder[0]!.Running) agent = holder[0];
            }
        }

        private sealed class Listener : AgentProcess.IListener
        {
            private readonly Host host;
            private readonly AgentProcess?[] holder;

            public Listener(Host host, AgentProcess?[] holder)
            {
                this.host = host;
                this.holder = holder;
            }

            public void OnEvent(Protocol.Event e) => host.OnAgentEvent(holder, e);
            public void OnLog(string line) => host.ide.Log(line);
            public void OnExit(int? code, bool expected, string? error) => host.OnAgentExit(holder, code, expected, error);
        }

        /// <summary>Never waits for the host's lock (safe on a UI thread).</summary>
        public bool Running => agent?.Running ?? false;

        /// <summary>The project or Visual Studio closes: stop cmcoder (H3).</summary>
        public void Dispose()
        {
            AgentProcess? a;
            lock (gate)
            {
                a = agent;
                agent = null;
                CloseAllDiffs();
            }
            a?.Stop();
        }

        private void StopAgent()
        {
            var a = agent;
            agent = null;
            CloseAllDiffs();
            a?.Stop();
        }

        private void OnAgentExit(AgentProcess?[] holder, int? code, bool expected, string? error)
        {
            lock (gate)
            {
                var which = holder[0];
                if (which != agent && which != null) return; // an old process, already replaced
                if (which == agent) agent = null;
                CloseAllDiffs();
                ide.Log(config.Product + " exited (code " + code + ")");
                if (!expected)
                {
                    SetState("exited", error ?? config.Product + " stopped unexpectedly (exit code " + code + "). See the "
                        + config.Product + " log (View > Output).");
                }
            }
        }

        // -- events from cmcoder (H5, H6, H8, H9, H11) ------------------------------------------

        private void OnAgentEvent(AgentProcess?[] holder, Protocol.Event e)
        {
            lock (gate)
            {
                if (holder[0] == null || holder[0] != agent) return; // an old process
                // The panel sees every event first and in order; side effects come after.
                ide.ToPanel("{\"kind\":\"event\",\"event\":" + e.Raw + "}");
                navigator.OnEvent(e);
                Action<Protocol.Event>[] ls;
                lock (listeners) ls = listeners.ToArray();
                foreach (var l in ls)
                {
                    try { l(e); }
                    catch (Exception ex) { ide.Log("A listener failed on " + e.Type + ": " + ex.Message); }
                }
                switch (e.Type)
                {
                    case "system_init":
                    {
                        var version = Json.Num(e.Fields, "protocol_version", -1);
                        if (version != Protocol.Version)
                        {
                            SetState("exited", "This extension and the cmcoder program it started don't match (protocol "
                                + version + ", the extension speaks " + Protocol.Version
                                + "). Install the extension again, or clear the cmcoder program setting.");
                            StopAgent();
                            return;
                        }
                        SetState("ready", null);
                        Send(Protocol.IdeCapabilities(ide.IdeTools));
                        UpdateContext();
                        break;
                    }
                    case "permission_request":
                    {
                        var change = Protocol.FileChange.Of(e.Obj("change"));
                        var id = e.Str("request_id");
                        if (change != null && id != null)
                        {
                            changes[id] = change;
                            if (ide.DiffReview) OpenDiff(id, change);
                        }
                        break;
                    }
                    case "ide_tool_request":
                    {
                        var id = e.Str("request_id");
                        var name = e.Str("name");
                        if (id == null || name == null) break;
                        var current = agent;
                        ide.RunIdeTool(name, e.Obj("input") ?? new JObject(),
                            (content, isError) => current?.Send(Protocol.IdeToolResult(id, content, isError)));
                        break;
                    }
                    case "result":
                        CloseAllDiffs(); // an interrupted turn leaves no open requests
                        break;
                    case "rewind_points":
                        ide.PickRewind(Protocol.RewindPoint.Of(e), c => Send(Protocol.Rewind(c.Turn, c.Code, c.Conversation, c.Outside)));
                        break;
                }
            }
        }

        private void OpenDiff(string id, Protocol.FileChange change)
        {
            openDiffs.Add(id);
            ide.OpenDiff(id, change);
        }

        private void CloseAllDiffs()
        {
            foreach (var id in openDiffs.ToList()) ide.CloseDiff(id);
            openDiffs.Clear();
            changes.Clear();
        }

        // -- the chat page (H5, H10, H12, H13, H18) --------------------------------------------

        /// <summary>A message (JSON text) from the chat page. Anything malformed is ignored.</summary>
        public void OnPanelMessage(string json)
        {
            lock (gate)
            {
                var m = Json.ParseObject(json);
                var kind = Json.Str(m, "kind");
                if (kind == null) return;
                switch (kind)
                {
                    case "ready":
                        // The page (re)loaded: start a conversation if none is running.
                        if (agent == null) Start(new string[0]);
                        else ide.ToPanel(StateMessage(state, null));
                        UpdateContext();
                        break;
                    case "send":
                    {
                        var text = Json.Str(m, "text") ?? "";
                        var images = Protocol.Images(m?["images"] as JArray);
                        if (text.Length > 0 || images.Count > 0) SendText(text, Json.Bool(m, "includeContext"), images);
                        break;
                    }
                    case "pasteImage":
                        break; // WebView2 gives the page clipboard images itself
                    case "panelError":
                        ide.Log(config.Product + " chat panel error: " + Json.Str(m, "message"));
                        break;
                    case "panelState":
                        ide.Log(config.Product + " chat panel: " + Json.Str(m, "state"));
                        break;
                    case "interrupt":
                        Send(Protocol.Interrupt());
                        break;
                    case "stopSubagent":
                    {
                        var id = Json.Str(m, "id");
                        if (id != null) Send(Protocol.StopSubagent(id));
                        break;
                    }
                    case "openNavigator":
                        ide.OpenNavigator();
                        break;
                    case "permission":
                    {
                        var id = Json.Str(m, "requestId");
                        if (id != null) Answer(id, Json.Bool(m, "allow"), Json.Bool(m, "remember"), Json.Str(m, "feedback"));
                        break;
                    }
                    case "showDiff":
                    {
                        var id = Json.Str(m, "requestId");
                        if (id != null && changes.TryGetValue(id, out var change)) OpenDiff(id, change);
                        break;
                    }
                    case "setMode":
                    {
                        var mode = Json.Str(m, "mode");
                        if (mode != null && Modes.Contains(mode)) Send(Protocol.SetMode(mode));
                        break;
                    }
                    case "setCritique":
                        Send(Protocol.SetCritique(Json.Bool(m, "enabled")));
                        break;
                    case "newConversation":
                        NewConversation(new string[0]);
                        break;
                    case "listSessions":
                        Send(Protocol.ListSessions());
                        break;
                    case "listCommands":
                        Send(Protocol.ListCommands());
                        break;
                    case "resume":
                    {
                        var id = Json.Str(m, "id");
                        if (id != null && SessionId.IsMatch(id)) NewConversation(new[] { "--resume=" + id });
                        break;
                    }
                    case "attachFile":
                        ide.AttachFile(path => ide.ToPanel(Json.Write(new Dictionary<string, object?>
                        {
                            ["kind"] = "prefill",
                            ["text"] = "@" + path.Replace('\\', '/') + " ",
                        })));
                        break;
                    case "restart":
                        Start(new string[0]);
                        break;
                    case "openLink":
                    {
                        var href = Json.Str(m, "href");
                        if (href != null && WebLink.IsMatch(href)) ide.OpenExternal(href);
                        break;
                    }
                }
            }
        }

        /// <summary>Sends a message, with the editor context when asked and allowed (H7).</summary>
        public bool SendText(string text, bool includeContext) => SendText(text, includeContext, new List<JObject>());

        /// <summary>A message with images the user attached (see <see cref="Protocol.Images"/>).</summary>
        public bool SendText(string text, bool includeContext, IList<JObject> images)
        {
            lock (gate)
            {
                var context = includeContext && ide.AutoContext ? ide.EditorContext() : null;
                if (Send(Protocol.UserMessage(text, context, images)))
                {
                    navigator.StartTurn(text.Length > 0 ? text : $"({images.Count} image{(images.Count == 1 ? "" : "s")})");
                    return true;
                }
                SetState("exited", config.Product + " isn't running. Click Restart.");
                return false;
            }
        }

        /// <summary>Answers a permission request, from the panel or the IDE's diff viewer (H10).</summary>
        public void Answer(string requestId, bool allow, bool remember, string? feedback)
        {
            lock (gate)
            {
                Send(Protocol.PermissionResponse(requestId, allow, remember, feedback));
                changes.Remove(requestId);
                if (openDiffs.Remove(requestId)) ide.CloseDiff(requestId);
                ide.ToPanel(Json.Write(new Dictionary<string, object?>
                {
                    ["kind"] = "permissionAnswered",
                    ["requestId"] = requestId,
                    ["text"] = allow ? (remember ? "Allowed (always)" : "Allowed") : "Denied",
                }));
            }
        }

        public void NewConversation(IList<string> extraArgs)
        {
            lock (gate)
            {
                StopAgent();
                ide.ToPanel("{\"kind\":\"reset\"}");
                navigator.Reset();
                Start(extraArgs);
            }
        }

        public void Interrupt()
        {
            lock (gate) Send(Protocol.Interrupt());
        }

        /// <summary>"Ask about selection": the context, then a prompt in the input box (H14).</summary>
        public void AskAboutSelection()
        {
            lock (gate)
            {
                ide.FocusChat();
                UpdateContext();
                ide.ToPanel(Json.Write(new Dictionary<string, object?> { ["kind"] = "prefill", ["text"] = "Explain the selected code. " }));
            }
        }

        /// <summary>The editor's file, selection or problems changed: refresh the chat's context label (H7).</summary>
        public void UpdateContext()
        {
            lock (gate)
            {
                var label = ide.AutoContext ? EditorContext.Label(ide.EditorContext()) : null;
                ide.ToPanel(Json.Write(new Dictionary<string, object?> { ["kind"] = "context", ["label"] = label }));
            }
        }

        /// <summary>Any protocol message; false when cmcoder isn't running.</summary>
        /// <remarks>
        /// Never waits for the host's lock (the process has its own): a UI thread
        /// may send (code search's dialogs) while the host waits for that UI thread.
        /// </remarks>
        public bool Send(string json)
        {
            var a = agent;
            return a != null && a.Send(json);
        }

        /// <summary>Never waits for the host's lock (safe on a UI thread).</summary>
        public string State => state;

        private void SetState(string s, string? message)
        {
            state = s;
            if (message != null) ide.Log(config.Product + ": " + message);
            ide.ToPanel(StateMessage(s, message));
        }

        private static string StateMessage(string s, string? message)
        {
            var m = new Dictionary<string, object?> { ["kind"] = "state", ["state"] = s };
            if (message != null) m["message"] = message;
            return Json.Write(m);
        }

        // -- the Agent Navigator (H15) ------------------------------------------------------------

        /// <summary>A message from the navigator page.</summary>
        public void OnNavigatorMessage(string json)
        {
            lock (gate)
            {
                var m = Json.ParseObject(json);
                switch (Json.Str(m, "kind"))
                {
                    case "ready":
                        navigator.Replay();
                        break;
                    case "stopSubagent":
                        var id = Json.Str(m, "id");
                        if (id != null) Send(Protocol.StopSubagent(id));
                        break;
                    case "openChat":
                        ide.FocusChat();
                        break;
                }
            }
        }

        /// <summary>Keeps the current turn's map events, so a navigator opened mid-turn shows everything so far.</summary>
        private sealed class Navigator
        {
            private static readonly HashSet<string> MapEvents = new HashSet<string>
                { "subagent_status", "tool_use", "tool_result", "permission_denied", "result" };
            private const int MaxEvents = 5000;
            private readonly IIde ide;
            private readonly LinkedList<string> turn = new LinkedList<string>();
            private string prompt = "";
            private string model = "";
            private bool busy;

            public Navigator(IIde ide) => this.ide = ide;

            public void StartTurn(string text)
            {
                turn.Clear();
                prompt = text;
                busy = true;
                ide.ToNavigator(ResetMessage());
            }

            public void Reset()
            {
                turn.Clear();
                prompt = "";
                busy = false;
                ide.ToNavigator(ResetMessage());
            }

            public void OnEvent(Protocol.Event e)
            {
                if (e.Type == "system_init" || e.Type == "model_changed")
                {
                    var m = e.Str("model");
                    if (m != null) model = m;
                }
                if (!MapEvents.Contains(e.Type)) return;
                if (e.Type == "result") busy = false;
                var message = "{\"kind\":\"event\",\"event\":" + e.Raw + "}";
                turn.AddLast(message);
                while (turn.Count > MaxEvents) turn.RemoveFirst();
                ide.ToNavigator(message);
            }

            public void Replay()
            {
                ide.ToNavigator(ResetMessage());
                foreach (var m in turn) ide.ToNavigator(m);
            }

            private string ResetMessage() => Json.Write(new Dictionary<string, object?>
                { ["kind"] = "reset", ["prompt"] = prompt, ["model"] = model, ["busy"] = busy });
        }
    }
}
