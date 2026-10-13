using EnvDTE;
using EnvDTE80;
using Microsoft.VisualStudio.Shell;
using Microsoft.VisualStudio.Shell.Interop;
using System;
using System.ComponentModel.Design;
using System.Runtime.InteropServices;
using System.Threading;
using Task = System.Threading.Tasks.Task;

namespace Cmcoder.VisualStudio
{
    internal static class Ids
    {
        public const string PackageString = "3f6b8a52-9c1e-4d7a-b5e2-7c4f1a9d2e63";
        public static readonly Guid CmdSet = new Guid("a4c2e7d1-5b3f-4e8a-9d6c-2f1b7e4a8c95");
        public const int OpenChat = 0x0100;
        public const int AskAboutSelection = 0x0101;
        public const int NewConversation = 0x0102;
        public const int Stop = 0x0103;
        public const int OpenNavigator = 0x0104;
        public const int CodeSearch = 0x0105;
        public const int ShowLog = 0x0106;
        public const int CopyDiagnostics = 0x0107;
        public const int AskFromEditor = 0x0108;
    }

    /// <summary>
    /// The extension: its commands, tool windows and options. Loads in the
    /// background when a command or tool window is first used.
    /// </summary>
    [PackageRegistration(UseManagedResourcesOnly = true, AllowsBackgroundLoading = true)]
    [Guid(Ids.PackageString)]
    [ProvideMenuResource("Menus.ctmenu", 1)]
    [ProvideToolWindow(typeof(ChatWindow), Style = VsDockStyle.Tabbed, Window = "3ae79031-e1bc-11d0-8f78-00a0c9110057")]
    [ProvideToolWindow(typeof(NavigatorWindow), Style = VsDockStyle.MDI, MultiInstances = false)]
    [ProvideOptionPage(typeof(Options), "cmcoder", "General", 0, 0, true)]
#if CMCODER_GATE
    [ProvideAutoLoad(Microsoft.VisualStudio.VSConstants.UICONTEXT.ShellInitialized_string, PackageAutoLoadFlags.BackgroundLoad)]
#endif
    public sealed class CmcoderPackage : AsyncPackage
    {
        internal static CmcoderPackage? Instance { get; private set; }

        private SolutionEvents? solutionEvents; // kept: DTE events stop when collected

        internal Options Options => (Options)GetDialogPage(typeof(Options));

        protected override async Task InitializeAsync(CancellationToken cancellationToken, IProgress<ServiceProgressData> progress)
        {
            await JoinableTaskFactory.SwitchToMainThreadAsync(cancellationToken);
            Instance = this;
            var commands = (OleMenuCommandService?)await GetServiceAsync(typeof(IMenuCommandService));
            if (commands != null)
            {
                Add(commands, Ids.OpenChat, () => ShowChat(true));
                Add(commands, Ids.AskAboutSelection, AskAboutSelection);
                Add(commands, Ids.AskFromEditor, AskAboutSelection);
                Add(commands, Ids.NewConversation, () =>
                {
                    ShowChat(false);
                    Session.Get().Run(h => h.NewConversation(new string[0]));
                });
                Add(commands, Ids.Stop, () => Session.Get().Run(h => h.Interrupt()));
                Add(commands, Ids.OpenNavigator, () => ShowNavigator());
                Add(commands, Ids.CodeSearch, () => CodeSearchUi.Menu(Session.Get().CodeSearch));
                Add(commands, Ids.ShowLog, () => Session.Get().Log.Show());
                Add(commands, Ids.CopyDiagnostics, () => Session.Get().CopyDiagnostics());
            }
            // A solution or folder closes: cmcoder stops; one opens: a new conversation there (H3).
            if (await GetServiceAsync(typeof(DTE)) is DTE2 dte)
            {
                solutionEvents = dte.Events.SolutionEvents;
                solutionEvents.AfterClosing += () => Session.Get().Run(h => h.Dispose());
                solutionEvents.Opened += () => Session.Get().SolutionOpened();
            }
#if CMCODER_GATE
            Gate.StartIfAsked(this);
#endif
        }

        private void Add(OleMenuCommandService commands, int id, Action run)
        {
            commands.AddCommand(new MenuCommand((s, e) =>
            {
                try
                {
                    run();
                }
                catch (Exception ex)
                {
                    Session.Get().Log.Line("The command failed: " + ex);
                }
            }, new CommandID(Ids.CmdSet, id)));
        }

        private void AskAboutSelection()
        {
            ShowChat(false);
            Session.Get().Run(h => h.AskAboutSelection());
        }

        /// <summary>Shows the chat (UI thread).</summary>
        internal void ShowChat(bool activate)
        {
            JoinableTaskFactory.RunAsync(async () =>
            {
                var window = await ShowToolWindowAsync(typeof(ChatWindow), 0, true, DisposalToken);
                if (activate && window?.Frame is IVsWindowFrame frame)
                {
                    await JoinableTaskFactory.SwitchToMainThreadAsync();
                    frame.Show();
                }
            });
        }

        internal void ShowNavigator()
        {
            JoinableTaskFactory.RunAsync(async () => await ShowToolWindowAsync(typeof(NavigatorWindow), 0, true, DisposalToken));
        }

        protected override void Dispose(bool disposing)
        {
            if (disposing) Session.DisposeAll();
            base.Dispose(disposing);
        }
    }
}
