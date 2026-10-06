using Cmcoder.Core;
using Microsoft.VisualStudio.PlatformUI;
using Microsoft.VisualStudio.Shell;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;
using Newtonsoft.Json;
using System;
using System.Collections.Generic;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Media;

namespace Cmcoder.VisualStudio
{
    /// <summary>
    /// One of the shared pages (chat or navigator) in WebView2: the page the
    /// web-panel README describes, the extension's own files only (a virtual
    /// host name, no file://), messages both ways, Visual Studio's theme
    /// (H5, H16, H18, H19, H20). UI thread only.
    /// </summary>
    internal sealed class Panel : DockPanel
    {
        /// <summary>The extension's panel files, served under this name (never on the network).</summary>
        internal const string FilesHost = "panel.cmcoder.example";

        /// <summary>The generated pages (the nonce changes on every start).</summary>
        internal const string PagesHost = "pages.cmcoder.example";

        private readonly string page;
        private readonly Action<string> onMessage;
        private readonly WebView2 view = new WebView2();
        private bool loaded;

        public Panel(string page, Action<string> onMessage)
        {
            this.page = page;
            this.onMessage = onMessage;
            LastChildFill = true;
        }

        /// <summary>A strip above the page (the chat's code search item).</summary>
        public void AddTop(UIElement element)
        {
            SetDock(element, Dock.Top);
            Children.Insert(0, element);
        }

        internal WebView2 View => view;

        internal string PageUrl => "https://" + PagesHost + "/" + page + ".html";

        /// <summary>Starts WebView2 and loads the page; the caller has registered this panel first.</summary>
        public async System.Threading.Tasks.Task LoadAsync()
        {
            await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
            Children.Add(view);
            try
            {
                // WebView2's data next to the user's other cmcoder files (the default,
                // next to devenv.exe, isn't writable).
                var data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "cmcoder", "webview2");
                var env = await CoreWebView2Environment.CreateAsync(null, data);
                await view.EnsureCoreWebView2Async(env);
            }
            catch (Exception e) when (e is WebView2RuntimeNotFoundException || e is System.Runtime.InteropServices.COMException || e is IOException)
            {
                Children.Remove(view);
                Children.Add(new TextBlock
                {
                    Text = Brand.Product + " needs the Microsoft Edge WebView2 Runtime (part of Windows 11 and Microsoft Edge). "
                        + "Install it, then restart Visual Studio.\n\n" + e.Message,
                    TextWrapping = TextWrapping.Wrap,
                    Margin = new Thickness(8),
                });
                return;
            }
            var web = view.CoreWebView2;
            var s = web.Settings;
#if CMCODER_GATE
            s.AreDevToolsEnabled = true;
#else
            s.AreDevToolsEnabled = false;
#endif
            s.AreDefaultContextMenusEnabled = false;
            s.IsStatusBarEnabled = false;
            s.AreHostObjectsAllowed = false;
            s.IsWebMessageEnabled = true;
            s.IsZoomControlEnabled = false;
            // DenyCors: the page (on PagesHost) may load these as scripts, styles and
            // images, but no script can fetch or read them across origins. (Deny would
            // block even the page's own <script> and <link>: an empty page.)
            web.SetVirtualHostNameToFolderMapping(FilesHost, Path.Combine(Brand.ExtensionDir, "panel"), CoreWebView2HostResourceAccessKind.DenyCors);
            web.SetVirtualHostNameToFolderMapping(PagesHost, WritePages(), CoreWebView2HostResourceAccessKind.Deny);
            // Never leave the page; links go through openLink (H18). No new windows.
            web.NavigationStarting += (_, e) =>
            {
                if (!SamePage(e.Uri)) e.Cancel = true;
            };
            web.NewWindowRequested += (_, e) => e.Handled = true;
            web.WebMessageReceived += (_, e) =>
            {
                if (!SamePage(e.Source)) return; // only from our page (H19)
                loaded = true; // it says "ready" before the navigation completes
                string json;
                try
                {
                    json = e.WebMessageAsJson;
                }
                catch (ArgumentException)
                {
                    return;
                }
                onMessage(json);
            };
            web.NavigationCompleted += (_, e) =>
            {
                loaded = true;
                ApplyTheme();
            };
            VSColorTheme.ThemeChanged += OnThemeChanged;
            web.Navigate(PageUrl);
        }

        private void OnThemeChanged(ThemeChangedEventArgs e) => ApplyTheme();

        public void Close()
        {
            VSColorTheme.ThemeChanged -= OnThemeChanged;
            view.Dispose();
        }

        private bool SamePage(string? url)
        {
            if (url == null) return false;
            var hash = url.IndexOf('#');
            if (hash >= 0) url = url.Substring(0, hash);
            return string.Equals(url, PageUrl, StringComparison.OrdinalIgnoreCase);
        }

        /// <summary>A message (JSON) to the page; any thread. Dropped while the page loads: it asks again with "ready".</summary>
        public void Post(string json)
        {
            Ui.Later(async () =>
            {
                await ThreadHelper.JoinableTaskFactory.SwitchToMainThreadAsync();
                if (!loaded || view.CoreWebView2 == null) return;
                // The JSON travels as a string literal and is parsed in the page.
                await view.CoreWebView2.ExecuteScriptAsync("window.postMessage(JSON.parse(" + JsonConvert.ToString(json) + "),'*')");
            });
        }

        /// <summary>Script in the page, its result as JSON (tests).</summary>
        internal System.Threading.Tasks.Task<string> EvaluateAsync(string script) => view.CoreWebView2.ExecuteScriptAsync(script);

        // -- the pages -----------------------------------------------------------------------------

        /// <summary>Writes chat.html and navigator.html (a new nonce each start) to the user's own folder.</summary>
        private static string WritePages()
        {
            var folder = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "cmcoder", "pages");
            Directory.CreateDirectory(folder);
            foreach (var name in new[] { "chat", "navigator" })
                File.WriteAllText(Path.Combine(folder, name + ".html"), Html(name), new UTF8Encoding(false));
            return folder;
        }

        internal static string Html(string page)
        {
            var bytes = new byte[18];
            using (var rng = RandomNumberGenerator.Create()) rng.GetBytes(bytes);
            var nonce = Convert.ToBase64String(bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_');
            var source = "https://" + FilesHost;
#if CMCODER_GATE
            var testScript = "\n  <script nonce=\"" + nonce + "\" src=\"" + source + "/test-driver.js\"></script>";
#else
            var testScript = "";
#endif
            return "<!DOCTYPE html>\n<html lang=\"en\"><head>\n"
                + "  <meta charset=\"UTF-8\">\n"
                + "  <meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src " + source
                + "; img-src " + source + " data:; script-src 'nonce-" + nonce + "'\">\n"
                + "  <link href=\"" + source + "/" + page + ".css\" rel=\"stylesheet\">\n"
                + "</head>\n<body data-product=\"" + Attr(Brand.Product) + "\" data-icon=\"" + source + "/icon.png\">\n"
                + "  <div id=\"app\"></div>\n"
                + "  <script nonce=\"" + nonce + "\" src=\"" + source + "/" + page + ".js\"></script>" + testScript + "\n"
                + "</body></html>\n";
        }

        private static string Attr(string s) => s.Replace("&", "&amp;").Replace("\"", "&quot;").Replace("<", "&lt;").Replace(">", "&gt;");

        // -- the theme (H20) ------------------------------------------------------------------------

        private void ApplyTheme()
        {
            if (view.CoreWebView2 == null) return;
            var vars = ThemeVariables();
            var js = new StringBuilder("(function(){var s=document.documentElement.style;");
            foreach (var v in vars)
                js.Append("s.setProperty(").Append(JsonConvert.ToString(v.Key)).Append(',').Append(JsonConvert.ToString(v.Value)).Append(");");
            js.Append("})()");
            _ = view.CoreWebView2.ExecuteScriptAsync(js.ToString());
        }

        /// <summary>theme.json's defaults for a light or dark theme, with Visual Studio's own colours and font.</summary>
        internal static Dictionary<string, string> ThemeVariables()
        {
            var bg = VSColorTheme.GetThemedColor(EnvironmentColors.ToolWindowBackgroundColorKey);
            var fg = VSColorTheme.GetThemedColor(EnvironmentColors.ToolWindowTextColorKey);
            var dark = (0.2126 * bg.R + 0.7152 * bg.G + 0.0722 * bg.B) / 255.0 < 0.5;
            var vars = new Dictionary<string, string>();
            try
            {
                var all = Json.Obj(Json.ParseObject(File.ReadAllText(Path.Combine(Brand.ExtensionDir, "panel", "theme.json"))), "variables");
                if (all != null)
                    foreach (var p in all.Properties())
                    {
                        var value = Json.Str(p.Value as Newtonsoft.Json.Linq.JObject, dark ? "dark" : "light");
                        if (value != null) vars[p.Name] = value;
                    }
            }
            catch (IOException)
            {
                // the panel's own defaults
            }
            vars["--vscode-sideBar-background"] = Hex(bg);
            vars["--vscode-foreground"] = Hex(fg);
            vars["--vscode-input-foreground"] = Hex(fg);
            vars["--vscode-button-secondaryForeground"] = Hex(fg);
            vars["--vscode-font-family"] = "\"" + SystemFonts.MessageFontFamily.Source + "\", \"Segoe UI\", sans-serif";
            return vars;
        }

        private static string Hex(System.Drawing.Color c) => "#" + c.R.ToString("x2") + c.G.ToString("x2") + c.B.ToString("x2");
    }
}
