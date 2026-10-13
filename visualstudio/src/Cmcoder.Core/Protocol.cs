using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Collections.Generic;

namespace Cmcoder.Core
{
    /// <summary>
    /// cmcoder's JSON-lines protocol (<c>cmcoder --protocol stdio</c>), as the
    /// IDE side uses it: the events it reads and the messages it sends. The same
    /// as the JVM core's Protocol; a test checks both against
    /// <c>cmcoder protocol-schema</c>.
    /// </summary>
    public static class Protocol
    {
        /// <summary>The protocol version this side speaks (system_init's protocol_version).</summary>
        public const int Version = 1;

        /// <summary>One field the IDE side reads: event type, field, JSON type.</summary>
        public sealed class Read
        {
            public Read(string @event, string field, string jsonType)
            {
                Event = @event;
                Field = field;
                JsonType = jsonType;
            }

            public string Event { get; }
            public string Field { get; }
            public string JsonType { get; }
        }

        public static readonly IReadOnlyList<Read> Reads = new[]
        {
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
            new Read("rag_setup_result", "message", "string"),
        };

        /// <summary>An event from cmcoder: its type, its fields, and the line as received.</summary>
        public sealed class Event
        {
            public Event(string type, JObject fields, string raw)
            {
                Type = type;
                Fields = fields;
                Raw = raw;
            }

            public string Type { get; }
            public JObject Fields { get; }

            /// <summary>The JSON text as cmcoder sent it (relayed to the panel unchanged).</summary>
            public string Raw { get; }

            public string? Str(string key) => Json.Str(Fields, key);
            public JObject? Obj(string key) => Json.Obj(Fields, key);

            /// <summary>A line from cmcoder's stdout as an event, or null if it isn't one.</summary>
            public static Event? Parse(string line)
            {
                var o = Json.ParseObject(line);
                var type = Json.Str(o, "type");
                return type == null ? null : new Event(type, o!, line);
            }
        }

        /// <summary>A proposed file change (permission_request.change), for the IDE's diff viewer.</summary>
        public sealed class FileChange
        {
            public FileChange(string path, string? before, string after)
            {
                Path = path;
                Before = before;
                After = after;
            }

            public string Path { get; }

            /// <summary>null for a new file.</summary>
            public string? Before { get; }
            public string After { get; }

            public static FileChange? Of(JObject? change)
            {
                var path = Json.Str(change, "path");
                var after = Json.Str(change, "after");
                return path == null || after == null ? null : new FileChange(path, Json.Str(change, "before"), after);
            }
        }

        /// <summary>An earlier message to rewind to (rewind_points).</summary>
        public sealed class RewindPoint
        {
            public RewindPoint(long turn, string text, long filesChanged, IReadOnlyList<string> outsideFiles)
            {
                Turn = turn;
                Text = text;
                FilesChanged = filesChanged;
                OutsideFiles = outsideFiles;
            }

            public long Turn { get; }
            public string Text { get; }
            public long FilesChanged { get; }
            public IReadOnlyList<string> OutsideFiles { get; }

            public static List<RewindPoint> Of(Event e)
            {
                var points = new List<RewindPoint>();
                foreach (var t in Json.List(e.Fields, "points"))
                {
                    if (t is not JObject p) continue;
                    points.Add(new RewindPoint(Json.Num(p, "turn", 0), Json.Str(p, "text") ?? "",
                        Json.Num(p, "files_changed", 0), Json.Strings(p, "outside_files")));
                }
                return points;
            }
        }

        // -- messages to cmcoder ------------------------------------------------------

        private static JObject Message(string type) => new JObject { ["type"] = type };

        private static string Text(JObject o) => o.ToString(Formatting.None);

        /// <summary>A user message; <paramref name="context"/> is the editor context (or null), from <see cref="EditorContext"/>.</summary>
        public static string UserMessage(string text, JObject? context) => UserMessage(text, context, new List<JObject>());

        /// <summary>A user message with images, each <c>{data, media_type, name}</c> (see <see cref="Images"/>).</summary>
        public static string UserMessage(string text, JObject? context, IList<JObject> images)
        {
            var m = Message("user_message");
            m["text"] = text;
            m["context"] = context ?? (JToken)JValue.CreateNull();
            if (images.Count > 0) m["images"] = new JArray(images);
            return Text(m);
        }

        /// <summary>The panel's images (<c>{data, mediaType, name}</c>) as the protocol's; cmcoder checks the bytes.</summary>
        public static IList<JObject> Images(JArray? fromPanel)
        {
            var output = new List<JObject>();
            if (fromPanel == null) return output;
            foreach (var item in fromPanel)
            {
                if (!(item is JObject i) || output.Count == 5) continue;
                var data = Json.Str(i, "data");
                if (string.IsNullOrEmpty(data)) continue;
                output.Add(new JObject
                {
                    ["data"] = data,
                    ["media_type"] = Json.Str(i, "mediaType"),
                    ["name"] = Json.Str(i, "name"),
                });
            }
            return output;
        }

        public static string Interrupt() => Text(Message("interrupt"));

        public static string Shutdown() => Text(Message("shutdown"));

        public static string PermissionResponse(string requestId, bool allow, bool remember, string? feedback)
        {
            var m = Message("permission_response");
            m["request_id"] = requestId;
            m["allow"] = allow;
            m["remember"] = remember;
            m["feedback"] = string.IsNullOrEmpty(feedback) ? JValue.CreateNull() : (JToken)feedback!;
            return Text(m);
        }

        public static string IdeCapabilities(IEnumerable<string> tools)
        {
            var m = Message("ide_capabilities");
            m["tools"] = new JArray(tools);
            return Text(m);
        }

        public static string IdeToolResult(string requestId, string content, bool isError)
        {
            var m = Message("ide_tool_result");
            m["request_id"] = requestId;
            m["content"] = content;
            m["is_error"] = isError;
            return Text(m);
        }

        public static string SetMode(string mode)
        {
            var m = Message("set_mode");
            m["mode"] = mode;
            return Text(m);
        }

        public static string SetCritique(bool enabled)
        {
            var m = Message("set_critique");
            m["enabled"] = enabled;
            m["save"] = true;
            return Text(m);
        }

        public static string StopSubagent(string id)
        {
            var m = Message("stop_subagent");
            m["id"] = id;
            return Text(m);
        }

        public static string ListSessions() => Text(Message("list_sessions"));

        public static string ListCommands() => Text(Message("list_commands"));

        public static string Rewind(long turn, bool code, bool conversation, bool outside)
        {
            var m = Message("rewind");
            m["turn"] = turn;
            m["code"] = code;
            m["conversation"] = conversation;
            m["outside"] = outside;
            return Text(m);
        }

        /// <summary>Code search: "status", "update", "rebuild" or "clear".</summary>
        public static string Index(string action)
        {
            var m = Message("index");
            m["action"] = action;
            return Text(m);
        }

        /// <summary>Code search: the gateways' models, to pick an embedding model (answer: rag_candidates).</summary>
        public static string RagCandidates() => Text(Message("rag_candidates"));

        /// <summary>
        /// Code search set-up, as <c>cmcoder rag setup</c> (answer: rag_setup_result).
        /// The API key goes to cmcoder only, which keeps it in the OS keychain.
        /// </summary>
        public static string RagSetup(string model, string store, string? url, string? apiKey, string scope, bool readOnly, bool indexNow)
        {
            var m = Message("rag_setup");
            m["embedding_model"] = model;
            m["store"] = store;
            m["url"] = url == null ? JValue.CreateNull() : (JToken)url;
            m["api_key"] = string.IsNullOrEmpty(apiKey) ? JValue.CreateNull() : (JToken)apiKey!;
            m["scope"] = scope;
            m["read_only"] = readOnly;
            m["index_now"] = indexNow;
            return Text(m);
        }

        /// <summary>Every message builder with sample arguments (for the schema test).</summary>
        public static IReadOnlyList<string> Samples()
        {
            var context = EditorContext.Of("/p/a.py", EditorContext.Selection("/p/a.py", 3, 5, "x = 1"),
                new[] { EditorContext.Diagnostic("/p/a.py", 3, "error", "bad", "pyright") });
            return new[]
            {
                UserMessage("hello", context),
                UserMessage("hello", null),
                UserMessage("", null, Images(new JArray(new JObject { ["data"] = "iVBORw0KGgo=", ["mediaType"] = "image/png", ["name"] = "shot.png" }))),
                Interrupt(),
                Shutdown(),
                PermissionResponse("r1", true, false, null),
                PermissionResponse("r1", false, false, "use b.py"),
                IdeCapabilities(new[] { "getDiagnostics", "openFile" }),
                IdeToolResult("t1", "No problems.", false),
                SetMode("acceptEdits"),
                SetCritique(true),
                StopSubagent("s1"),
                ListSessions(),
                ListCommands(),
                Rewind(2, true, true, false),
                Index("update"),
                RagCandidates(),
                RagSetup("corp:bge-m3", "chroma-server", "https://chroma.example:8000", "k", "user", true, false),
                RagSetup("corp:bge-m3", "local", null, null, "project", false, true),
            };
        }
    }
}
