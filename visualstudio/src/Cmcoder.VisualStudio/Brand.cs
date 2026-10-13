using Cmcoder.Core;
using Microsoft.VisualStudio.Shell;
using System.IO;

namespace Cmcoder.VisualStudio
{
    /// <summary>UI work started from anywhere that nobody waits for: failures are reported, never lost.</summary>
    internal static class Ui
    {
#pragma warning disable VSSDK007 // the one place UI work is started without waiting: its failures are filed
        public static void Later(System.Func<System.Threading.Tasks.Task> work) =>
            ThreadHelper.JoinableTaskFactory.RunAsync(work).FileAndForget("cmcoder/ui");
#pragma warning restore VSSDK007
    }

    /// <summary>The extension's folder, and the company's product name (branding/brand.json, in panel/).</summary>
    internal static class Brand
    {
        private static string? product;

        /// <summary>Where the extension is installed (holds panel/ and bin/cmcoder/).</summary>
        public static string ExtensionDir => Path.GetDirectoryName(typeof(Brand).Assembly.Location)!;

        public static string Product
        {
            get
            {
                if (product != null) return product;
                product = "cmcoder";
                try
                {
                    var file = Path.Combine(ExtensionDir, "panel", "brand.json");
                    var name = Json.Str(Json.ParseObject(File.ReadAllText(file)), "productName");
                    if (!string.IsNullOrWhiteSpace(name)) product = name!.Trim();
                }
                catch (IOException)
                {
                    // the default name
                }
                return product;
            }
        }
    }
}
