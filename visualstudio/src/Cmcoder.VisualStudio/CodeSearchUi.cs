using Cmcoder.Core;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using System;
using System.Collections.Generic;
using System.Linq;
using System.Text.RegularExpressions;
using System.Threading.Tasks;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// Code search in Visual Studio (H16): the chat's status item opens this menu;
    /// the set-up asks what <c>cmcoder rag setup</c> asks. cmcoder does every
    /// step; waiting never blocks the UI thread.
    /// </summary>
    internal static class CodeSearchUi
    {
        private const string TypeAName = "Type a model name…";

        private static void Message(string text, OLEMSGICON icon) =>
            VsShellUtilities.ShowMessageBox(CmcoderPackage.Instance!, text, Brand.Product, icon,
                OLEMSGBUTTON.OLEMSGBUTTON_OK, OLEMSGDEFBUTTON.OLEMSGDEFBUTTON_FIRST);

        private static void NotRunning() => Message(Brand.Product + " isn't running: open its chat first.", OLEMSGICON.OLEMSGICON_WARNING);

        /// <summary>The status item was clicked (UI thread).</summary>
        public static void Menu(CodeSearch search)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (!search.IsSetUp)
            {
                SetUp(search);
                return;
            }
            var lines = string.Join("\n", search.Lines);
            switch (Dialogs.Choose(Brand.Product + ": Code search", lines, "Update the index", "Rebuild it", "Set up again", "Delete this solution's index"))
            {
                case 0: Index(search, "update"); break;
                case 1: Index(search, "rebuild"); break;
                case 2: SetUp(search); break;
                case 3:
                    if (Dialogs.Choose(Brand.Product, "Delete this solution's code index? It can be built again.", "Delete it") == 0)
                        Index(search, "clear");
                    break;
            }
        }

        private static void Index(CodeSearch search, string action) =>
            After(search.IndexAsync(action), status => { if (status == null) NotRunning(); });

        /// <summary>Embedding model, where the index lives, whose settings, index now.</summary>
        public static void SetUp(CodeSearch search)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            After(search.CandidatesAsync(), candidates =>
            {
                if (candidates == null)
                {
                    NotRunning();
                    return;
                }
                foreach (var e in candidates.Errors)
                    Message("Couldn't list the models of " + e.Key + ": " + e.Value, OLEMSGICON.OLEMSGICON_WARNING);
                var items = candidates.Likely.Select(m => m + "  (embedding model)").Concat(candidates.Other).Concat(new[] { TypeAName }).ToList();
                var models = candidates.Likely.Concat(candidates.Other).ToList();
                var picked = Dialogs.Pick("Code search (1/4): embedding model", "A model on your gateway that turns code into vectors:", items, filter: true);
                if (picked == null) return;
                var model = picked.Value < models.Count ? models[picked.Value]
                    : Dialogs.Ask("Embedding model", "provider:model, e.g. corp:bge-m3", false, v => v.Trim().Length == 0 ? "A model name" : null)?.Trim();
                if (model == null) return;

                var where = Dialogs.Choose("Code search (2/4): where the index lives", "Where should the index of this solution live?",
                    "On this machine", "Chroma on this machine", "A Chroma server");
                if (where == null) return;
                var store = new[] { "local", "chroma", "chroma-server" }[where.Value];
                string? url = null, apiKey = null;
                var readOnly = false;
                if (store == "chroma-server")
                {
                    url = Dialogs.Ask("Chroma server", "Its address, e.g. https://chroma.example.com:8000", false,
                        v => Regex.IsMatch(v.Trim(), @"^https?://\S+$") ? null : "An http:// or https:// address")?.Trim();
                    if (url == null) return;
                    apiKey = Dialogs.Ask("Chroma server API key", "Kept in your OS keychain, never in a file. Leave empty if it needs none.", true);
                    if (apiKey == null) return;
                    var mode = Dialogs.Choose("Chroma server: updates", "Should this solution update the index on the server?",
                        "Search and update it", "Only search it (someone else keeps it up to date)");
                    if (mode == null) return;
                    readOnly = mode == 1;
                }
                var scope = Dialogs.Choose("Code search (3/4): whose settings", "Save these settings for:",
                    "Just me (~/.cmcoder/settings.json)", "This solution (.cmcoder/settings.json, shared through git)");
                if (scope == null) return;
                var indexNow = false;
                if (!readOnly)
                {
                    var now = Dialogs.Choose("Code search (4/4)", "Index this solution now?", "Index it now", "Later");
                    if (now == null) return;
                    indexNow = now == 0;
                }
                After(search.SetUpAsync(model, store, url, apiKey, scope == 0 ? "user" : "project", readOnly, indexNow), result =>
                {
                    if (result == null) NotRunning();
                    else Message(result.Ok ? result.Message : "Code search: " + result.Message,
                        result.Ok ? OLEMSGICON.OLEMSGICON_INFO : OLEMSGICON.OLEMSGICON_CRITICAL);
                });
            });
        }

        /// <summary>Waits off the UI thread, then runs then on it.</summary>
        private static void After<T>(Task<T> task, Action<T> then)
        {
            Ui.Later(async () =>
            {
                var value = await task.ConfigureAwait(false);
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                then(value);
            });
        }
    }
}
