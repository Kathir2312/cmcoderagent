using Newtonsoft.Json.Linq;
using System;
using System.Collections.Generic;

namespace Cmcoder.Core
{
    /// <summary>
    /// What an IDE does for <see cref="Host"/>: everything that needs the IDE's
    /// own APIs. The Visual Studio extension implements it. Host calls these
    /// from its own threads and never waits for them, except
    /// <see cref="EditorContext"/>: an implementation hands UI work to the UI
    /// thread and returns.
    /// </summary>
    public interface IIde
    {
        /// <summary>Deliver a message (JSON) to the chat page.</summary>
        void ToPanel(string json);

        /// <summary>Deliver a message to the Agent Navigator page, if it is open.</summary>
        void ToNavigator(string json);

        /// <summary>Open (or show) the Agent Navigator (H15).</summary>
        void OpenNavigator();

        /// <summary>Bring the chat to the front.</summary>
        void FocusChat();

        /// <summary>The editor context now (H7; see <see cref="Core.EditorContext"/>), or null when no file is open.</summary>
        JObject? EditorContext();

        /// <summary>The setting "send the editor context with each message".</summary>
        bool AutoContext { get; }

        /// <summary>The setting "review changes in the diff viewer" (H9).</summary>
        bool DiffReview { get; }

        /// <summary>Show a proposed change in the IDE's diff viewer, with Accept and Reject (H9).</summary>
        void OpenDiff(string requestId, Protocol.FileChange change);

        /// <summary>Close the diff for a request (answered elsewhere, or the turn ended).</summary>
        void CloseDiff(string requestId);

        /// <summary>The IDE tools this IDE can run (H8).</summary>
        IReadOnlyList<string> IdeTools { get; }

        /// <summary>Run an IDE tool; call done(content, isError) on any thread.</summary>
        void RunIdeTool(string name, JObject input, Action<string, bool> done);

        /// <summary>/rewind: let the user choose a point and what to undo (H11); call chosen only if they did.</summary>
        void PickRewind(IReadOnlyList<Protocol.RewindPoint> points, Action<RewindChoice> chosen);

        /// <summary>The @ button: a project file, as a path relative to the project (H13).</summary>
        void AttachFile(Action<string> chosen);

        /// <summary>Open an http(s) address in the system browser (H18; Host has checked it).</summary>
        void OpenExternal(string url);

        /// <summary>A line for the extension's log.</summary>
        void Log(string line);
    }

    public sealed class RewindChoice
    {
        public RewindChoice(long turn, bool code, bool conversation, bool outside)
        {
            Turn = turn;
            Code = code;
            Conversation = conversation;
            Outside = outside;
        }

        public long Turn { get; }
        public bool Code { get; }
        public bool Conversation { get; }
        public bool Outside { get; }
    }
}
