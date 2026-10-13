using Microsoft.VisualStudio.Shell;
using System;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;

namespace Cmcoder.VisualStudio
{
    /// <summary>The chat (H5): the shared chat page, with code search's state above it (H16).</summary>
    [Guid("e2a9c4f7-1b6d-4e3a-8c5f-9d2b7a1e4c36")]
    public sealed class ChatWindow : ToolWindowPane
    {
        private readonly Panel panel;
        private readonly Button codeSearch = new Button { HorizontalAlignment = HorizontalAlignment.Left, Padding = new Thickness(6, 1, 6, 1), Margin = new Thickness(2) };
        private IDisposable? stopCodeSearch;

        public ChatWindow() : base(null)
        {
            Caption = Brand.Product;
            var session = Session.Get();
            panel = new Panel("chat", json => session.Run(h => h.OnPanelMessage(json)));
            codeSearch.Click += (_, __) => CodeSearchUi.Menu(session.CodeSearch);
            codeSearch.SetResourceReference(Control.StyleProperty, Microsoft.VisualStudio.Shell.VsResourceKeys.ButtonStyleKey);
            panel.AddTop(codeSearch);
            Content = panel;
            ShowCodeSearch();
        }

        protected override void Initialize()
        {
            base.Initialize();
            var session = Session.Get();
            // Registered before the page loads: its "ready" may come at once.
            session.ChatOpened(panel);
            stopCodeSearch = session.OnCodeSearchChange(() => Ui.Later(async () =>
            {
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                ShowCodeSearch();
            }));
            Ui.Later(() => panel.LoadAsync());
        }

        private void ShowCodeSearch()
        {
            var search = Session.Get().CodeSearch;
            var text = search.Text;
            codeSearch.Content = text ?? "Code search";
            codeSearch.ToolTip = text == null ? "Code search (when " + Brand.Product + " is running)" : search.Tooltip;
        }

        internal Panel Panel => panel;

        /// <summary>For tests: the code search item's text.</summary>
        internal string CodeSearchText => codeSearch.Content as string ?? "";

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                stopCodeSearch?.Dispose();
                Session.Get().ChatClosed(panel);
                panel.Close();
            }
            base.Dispose(disposing);
        }
    }

    /// <summary>The Agent Navigator (H15), a document tab: the current turn's agents.</summary>
    [Guid("5c8e1a3b-7d4f-4b2e-9a6c-3e7f1d9b5a28")]
    public sealed class NavigatorWindow : ToolWindowPane
    {
        private readonly Panel panel;

        public NavigatorWindow() : base(null)
        {
            Caption = Brand.Product + ": Agent Navigator";
            var session = Session.Get();
            panel = new Panel("navigator", json => session.Run(h => h.OnNavigatorMessage(json)));
            Content = panel;
        }

        protected override void Initialize()
        {
            base.Initialize();
            Session.Get().NavigatorOpened(panel);
            Ui.Later(() => panel.LoadAsync());
        }

        internal Panel Panel => panel;

        protected override void Dispose(bool disposing)
        {
            if (disposing)
            {
                Session.Get().NavigatorClosed(panel);
                panel.Close();
            }
            base.Dispose(disposing);
        }
    }
}
