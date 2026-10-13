using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;
using System.Globalization;
using System.Threading;
using System.Threading.Tasks;

namespace Cmcoder.Core
{
    /// <summary>
    /// Code search (H16): the index's state for a status item, and the requests
    /// behind its menu and set-up. As in VS Code (codeSearch.ts) and the JVM
    /// core, cmcoder does every step over the protocol, so the terminal and the
    /// IDEs write the same settings and use the same index.
    /// </summary>
    public sealed class CodeSearch
    {
        private readonly Host host;
        private readonly Action<CodeSearch> changed;
        private readonly object gate = new object();
        private readonly Dictionary<string, TaskCompletionSource<Protocol.Event?>> waiting = new Dictionary<string, TaskCompletionSource<Protocol.Event?>>();
        private JObject? status;
        private string? progress;

        /// <param name="changed">Told (any thread) when the status item should change.</param>
        public CodeSearch(Host host, Action<CodeSearch> changed)
        {
            this.host = host;
            this.changed = changed;
            host.AddListener(OnEvent);
        }

        private void OnEvent(Protocol.Event e)
        {
            TaskCompletionSource<Protocol.Event?>? done;
            lock (gate)
            {
                switch (e.Type)
                {
                    case "system_init":
                        status = null;
                        progress = null;
                        host.Send(Protocol.Index("status")); // for the status item
                        break;
                    case "index_status":
                        status = e.Fields;
                        progress = null;
                        break;
                    case "index_progress":
                        progress = Json.Num(e.Fields, "done", 0) + "/" + Json.Num(e.Fields, "total", 0) + " files";
                        break;
                }
                if (waiting.TryGetValue(e.Type, out done)) waiting.Remove(e.Type);
            }
            if (e.Type == "system_init" || e.Type.StartsWith("index_", StringComparison.Ordinal)) changed(this);
            // Completed off this thread: whoever waits mustn't run with the host locked.
            done?.TrySetResult(e);
        }

        /// <summary>Sends a message and gives the next event of that type: null when cmcoder isn't running or nothing came in time.</summary>
        public Task<Protocol.Event?> Request(string message, string type, TimeSpan timeout)
        {
            var tcs = new TaskCompletionSource<Protocol.Event?>(TaskCreationOptions.RunContinuationsAsynchronously);
            TaskCompletionSource<Protocol.Event?>? previous;
            lock (gate)
            {
                waiting.TryGetValue(type, out previous);
                waiting[type] = tcs;
            }
            previous?.TrySetResult(null);
            if (!host.Send(message))
            {
                lock (gate)
                {
                    if (waiting.TryGetValue(type, out var current) && current == tcs) waiting.Remove(type);
                }
                tcs.TrySetResult(null);
                return tcs.Task;
            }
            var timer = new Timer(_ => tcs.TrySetResult(null), null, timeout, Timeout.InfiniteTimeSpan);
            tcs.Task.ContinueWith(_ => timer.Dispose(), TaskScheduler.Default);
            return tcs.Task;
        }

        /// <summary>The gateways' models: likely embedding models first. Null if it didn't answer.</summary>
        public async Task<Candidates?> CandidatesAsync()
        {
            var e = await Request(Protocol.RagCandidates(), "rag_candidates", TimeSpan.FromSeconds(60)).ConfigureAwait(false);
            return e == null ? null : new Candidates(e);
        }

        public sealed class Candidates
        {
            internal Candidates(Protocol.Event e)
            {
                Likely = Json.Strings(e.Fields, "likely");
                Other = Json.Strings(e.Fields, "other");
                var errs = Json.Obj(e.Fields, "errors");
                if (errs != null)
                    foreach (var p in errs.Properties()) Errors[p.Name] = p.Value.ToString();
            }

            public List<string> Likely { get; }
            public List<string> Other { get; }
            public Dictionary<string, string> Errors { get; } = new Dictionary<string, string>();
        }

        /// <summary>Sets code search up (indexing can take long). Null if cmcoder isn't running.</summary>
        public async Task<SetupResult?> SetUpAsync(string model, string store, string? url, string? apiKey, string scope, bool readOnly, bool indexNow)
        {
            var e = await Request(Protocol.RagSetup(model, store, url, apiKey, scope, readOnly, indexNow), "rag_setup_result",
                TimeSpan.FromMinutes(30)).ConfigureAwait(false);
            return e == null ? null : new SetupResult(Json.Bool(e.Fields, "ok"), Json.Str(e.Fields, "message") ?? "");
        }

        /// <summary>"update", "rebuild" or "clear"; the new state when done (null if cmcoder isn't running).</summary>
        public Task<Protocol.Event?> IndexAsync(string action) => Request(Protocol.Index(action), "index_status", TimeSpan.FromMinutes(30));

        public sealed class SetupResult
        {
            internal SetupResult(bool ok, string message)
            {
                Ok = ok;
                Message = message;
            }

            public bool Ok { get; }
            public string Message { get; }
        }

        // -- the status item -------------------------------------------------------------------

        /// <summary>Whether code search is set up (false before cmcoder said).</summary>
        public bool IsSetUp
        {
            get { lock (gate) return status != null && Json.Bool(status, "set_up"); }
        }

        /// <summary>The status item's text; null: hide it (cmcoder hasn't said yet).</summary>
        public string? Text
        {
            get
            {
                lock (gate)
                {
                    if (progress != null) return "Indexing " + progress;
                    if (status == null) return null;
                    if (!Json.Bool(status, "set_up")) return "Code search: off";
                    var error = Json.Str(status, "error");
                    if (error != null && !Json.Bool(status, "active")) return "Code search: problem";
                    if (Json.Bool(status, "updating")) return "Code search: updating";
                    var files = Json.Num(status, "files", 0);
                    if (files == 0 && Json.Num(status, "chunks", 0) == 0) return "Code search: not indexed";
                    return "Code search: " + files.ToString("N0", CultureInfo.InvariantCulture) + " files";
                }
            }
        }

        /// <summary>The status item's tooltip.</summary>
        public string Tooltip
        {
            get
            {
                lock (gate)
                {
                    if (progress != null) return "Indexing this project: " + progress;
                    if (status == null) return "";
                    if (!Json.Bool(status, "set_up")) return "Click to set up code search (an index of this project for the model).";
                    var error = Json.Str(status, "error");
                    if (error != null && !Json.Bool(status, "active")) return error;
                    if (Json.Num(status, "files", 0) == 0 && Json.Num(status, "chunks", 0) == 0) return "Click to index this project.";
                    return string.Join("\n", Json.Strings(status, "lines")) + (error != null ? "\nLast update failed: " + error : "");
                }
            }
        }

        /// <summary>The index's description, one line per fact.</summary>
        public List<string> Lines
        {
            get { lock (gate) return Json.Strings(status, "lines"); }
        }
    }
}
