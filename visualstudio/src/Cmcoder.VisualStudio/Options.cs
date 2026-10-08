using Microsoft.VisualStudio.Shell;
using System.ComponentModel;

namespace Cmcoder.VisualStudio
{
    public enum PermissionModeSetting
    {
        [Description("cmcoder's default")] CmcoderDefault,
        [Description("Ask before edits and commands")] Default,
        [Description("Edits without asking")] AcceptEdits,
        [Description("Read only, plan first")] Plan,
        [Description("Ask only for risky actions")] Auto,
    }

    /// <summary>Tools > Options > cmcoder (H17, H21). Stored per user, never in a solution.</summary>
    public sealed class Options : DialogPage
    {
        [Category("cmcoder")]
        [DisplayName("cmcoder program")]
        [Description("Full path of cmcoder.exe. Leave empty to use the one inside this extension.")]
        public string Program { get; set; } = "";

        [Category("cmcoder")]
        [DisplayName("Permission mode at start")]
        [Description("How cmcoder asks before it changes files or runs commands.")]
        public PermissionModeSetting PermissionMode { get; set; } = PermissionModeSetting.CmcoderDefault;

        [Category("cmcoder")]
        [DisplayName("Send the editor context")]
        [Description("Send the open file, the selection and its Error List entries with each message.")]
        public bool AutoContext { get; set; } = true;

        [Category("cmcoder")]
        [DisplayName("Review changes in the diff window")]
        [Description("Show proposed changes in Visual Studio's diff window, with Accept and Reject.")]
        public bool DiffReview { get; set; } = true;

        [Category("cmcoder")]
        [DisplayName("Use the solution's own .cmcoder settings")]
        [Description("Only for solutions you trust: they can change which commands are allowed.")]
        public bool TrustProject { get; set; }

        internal string? PermissionModeArgument => PermissionMode switch
        {
            PermissionModeSetting.Default => "default",
            PermissionModeSetting.AcceptEdits => "acceptEdits",
            PermissionModeSetting.Plan => "plan",
            PermissionModeSetting.Auto => "auto",
            _ => null,
        };
    }
}
