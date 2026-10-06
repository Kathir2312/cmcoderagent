using Cmcoder.Core;
using Microsoft.VisualStudio;
using Microsoft.VisualStudio.Imaging;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using System;
using System.Collections.Generic;
using System.IO;
using System.Text;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// Proposed changes in Visual Studio's diff window, with Accept, Accept
    /// Always and Reject in a bar above it (H9, H10). UI thread only.
    /// </summary>
    internal sealed class DiffReview
    {
        internal const string Accept = "Accept";
        internal const string AcceptAlways = "Accept Always";
        internal const string Reject = "Reject";

        private readonly Session session;
        private readonly Dictionary<string, Review> open = new Dictionary<string, Review>();

        public DiffReview(Session session) => this.session = session;

        private sealed class Review : IVsInfoBarUIEvents
        {
            public string RequestId = "";
            public IVsWindowFrame? Frame;
            public IVsInfoBarUIElement? Bar;
            public uint Cookie;
            public string Folder = "";
            public bool Answered;
            public Session? Session;

            public void OnClosed(IVsInfoBarUIElement infoBarUIElement) { }

            public void OnActionItemClicked(IVsInfoBarUIElement infoBarUIElement, IVsInfoBarActionItem actionItem)
            {
                if (Answered) return;
                Answered = true;
                var text = actionItem.Text;
                Session!.Answer(RequestId, text != Reject, text == AcceptAlways);
            }
        }

        public void Open(string requestId, Protocol.FileChange change)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (open.TryGetValue(requestId, out var existing) && existing.Frame != null)
            {
                existing.Frame.Show();
                return;
            }
            var name = Path.GetFileName(change.Path);
            if (string.IsNullOrEmpty(name)) name = "file";
            // Both sides as read-only temporary files (the diff window compares files).
            var folder = Path.Combine(Path.GetTempPath(), "cmcoder", "diff", requestId.Replace(':', '_'));
            var left = Path.Combine(folder, "now", name);
            var right = Path.Combine(folder, "proposed", name);
            Directory.CreateDirectory(Path.GetDirectoryName(left)!);
            Directory.CreateDirectory(Path.GetDirectoryName(right)!);
            File.WriteAllText(left, change.Before ?? "", new UTF8Encoding(false));
            File.WriteAllText(right, change.After, new UTF8Encoding(false));
            File.SetAttributes(left, FileAttributes.ReadOnly);
            File.SetAttributes(right, FileAttributes.ReadOnly);

            if (Package.GetGlobalService(typeof(SVsDifferenceService)) is not IVsDifferenceService diff) return;
            var caption = change.Before == null ? name + " (new file, proposed by " + Brand.Product + ")" : name + " ↔ proposed by " + Brand.Product;
            var frame = diff.OpenComparisonWindow2(left, right, caption, change.Path,
                change.Before == null ? "(no file yet)" : name + " (now)", name + " (proposed by " + Brand.Product + ")", caption, null,
                (uint)(__VSDIFFSERVICEOPTIONS.VSDIFFOPT_LeftFileIsTemporary | __VSDIFFSERVICEOPTIONS.VSDIFFOPT_RightFileIsTemporary));
            var review = new Review { RequestId = requestId, Frame = frame, Folder = folder, Session = session };
            open[requestId] = review;
            AddBar(review, change);
        }

        private static void AddBar(Review review, Protocol.FileChange change)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (review.Frame == null) return;
            if (review.Frame.GetProperty((int)__VSFPROPID7.VSFPROPID_InfoBarHost, out var hostObject) != VSConstants.S_OK) return;
            if (hostObject is not IVsInfoBarHost host) return;
            if (Package.GetGlobalService(typeof(SVsInfoBarUIFactory)) is not IVsInfoBarUIFactory factory) return;
            var model = new InfoBarModel(
                new[] { new InfoBarTextSpan(Brand.Product + " wants to " + (change.Before == null ? "create " : "change ") + change.Path + ".  ") },
                new[] { new InfoBarButton(Accept), new InfoBarButton(AcceptAlways), new InfoBarButton(Reject) },
                KnownMonikers.StatusInformation,
                isCloseButtonVisible: false);
            var bar = factory.CreateInfoBar(model);
            bar.Advise(review, out review.Cookie);
            host.AddInfoBar(bar);
            review.Bar = bar;
        }

        public void Close(string requestId)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (!open.TryGetValue(requestId, out var review)) return;
            open.Remove(requestId);
            review.Answered = true;
            try
            {
                review.Bar?.Unadvise(review.Cookie);
                review.Frame?.CloseFrame((uint)__FRAMECLOSE.FRAMECLOSE_NoSave);
            }
            catch (Exception)
            {
                // the user closed it already
            }
            try
            {
                foreach (var f in Directory.GetFiles(review.Folder, "*", SearchOption.AllDirectories)) File.SetAttributes(f, FileAttributes.Normal);
                Directory.Delete(review.Folder, true);
            }
            catch (IOException)
            {
                // still open somewhere: %TEMP% is cleaned anyway
            }
            catch (UnauthorizedAccessException)
            {
            }
        }

        /// <summary>For tests: the requests with a diff open.</summary>
        internal IReadOnlyCollection<string> OpenRequests => open.Keys;

        /// <summary>For tests: clicks a button of a request's bar, as the user would.</summary>
        internal void Click(string requestId, string button)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (!open.TryGetValue(requestId, out var review) || review.Answered) return;
            review.Answered = true;
            session.Answer(requestId, button != Reject, button == AcceptAlways);
        }
    }
}
