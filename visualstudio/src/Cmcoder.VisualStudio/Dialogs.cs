using Cmcoder.Core;
using Microsoft.VisualStudio.PlatformUI;
using Microsoft.VisualStudio.Shell;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace Cmcoder.VisualStudio
{
    /// <summary>The pickers the chat asks for (H11, H13) and the code search set-up's questions. UI thread only.</summary>
    internal static class Dialogs
    {
        /// <summary>One item of a list (null: cancelled). With filter: a box narrows the list as the user types.</summary>
        public static int? Pick(string title, string prompt, IList<string> items, bool filter = false)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var window = new DialogWindow { Title = title, Width = 640, Height = 460, WindowStartupLocation = WindowStartupLocation.CenterOwner, HasMinimizeButton = false };
            var panel = new DockPanel { Margin = new Thickness(10) };
            var label = new TextBlock { Text = prompt, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 6) };
            DockPanel.SetDock(label, Dock.Top);
            panel.Children.Add(label);
            var box = new TextBox { Margin = new Thickness(0, 0, 0, 6) };
            if (filter)
            {
                DockPanel.SetDock(box, Dock.Top);
                panel.Children.Add(box);
            }
            var buttons = Buttons(window, out var ok);
            DockPanel.SetDock(buttons, Dock.Bottom);
            panel.Children.Add(buttons);
            var list = new ListBox();
            panel.Children.Add(list);
            var shown = new List<int>();
            void Fill()
            {
                list.Items.Clear();
                shown.Clear();
                var words = box.Text.Trim().ToLowerInvariant().Split(new[] { ' ' }, StringSplitOptions.RemoveEmptyEntries);
                for (var i = 0; i < items.Count && shown.Count < 2000; i++)
                {
                    if (words.Any(w => !items[i].ToLowerInvariant().Contains(w))) continue;
                    shown.Add(i);
                    list.Items.Add(items[i]);
                }
                if (list.Items.Count > 0) list.SelectedIndex = 0;
            }
            box.TextChanged += (_, __) => Fill();
            list.MouseDoubleClick += (_, __) => { if (list.SelectedIndex >= 0) window.DialogResult = true; };
            ok.Click += (_, __) => { if (list.SelectedIndex >= 0) window.DialogResult = true; };
            window.Content = panel;
            Fill();
            window.Loaded += (_, __) => (filter ? (UIElement)box : list).Focus();
            return window.ShowModal() == true && list.SelectedIndex >= 0 ? shown[list.SelectedIndex] : (int?)null;
        }

        /// <summary>A question with its own answers (null: cancelled).</summary>
        public static int? Choose(string title, string message, params string[] answers)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var window = new DialogWindow { Title = title, SizeToContent = SizeToContent.WidthAndHeight, MaxWidth = 720, WindowStartupLocation = WindowStartupLocation.CenterOwner, HasMinimizeButton = false, HasMaximizeButton = false };
            var panel = new StackPanel { Margin = new Thickness(12) };
            panel.Children.Add(new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 10) });
            int? chosen = null;
            for (var i = 0; i < answers.Length; i++)
            {
                var index = i;
                var b = new Button { Content = answers[i], Margin = new Thickness(0, 2, 0, 2), Padding = new Thickness(8, 3, 8, 3), HorizontalContentAlignment = HorizontalAlignment.Left };
                b.Click += (_, __) =>
                {
                    chosen = index;
                    window.DialogResult = true;
                };
                panel.Children.Add(b);
            }
            var cancel = new Button { Content = "Cancel", IsCancel = true, Margin = new Thickness(0, 10, 0, 0), HorizontalAlignment = HorizontalAlignment.Right, Padding = new Thickness(12, 3, 12, 3) };
            panel.Children.Add(cancel);
            window.Content = panel;
            return window.ShowModal() == true ? chosen : null;
        }

        /// <summary>A line of text (null: cancelled); secret hides what's typed. validate returns an error or null.</summary>
        public static string? Ask(string title, string prompt, bool secret = false, Func<string, string?>? validate = null)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var window = new DialogWindow { Title = title, Width = 520, SizeToContent = SizeToContent.Height, WindowStartupLocation = WindowStartupLocation.CenterOwner, HasMinimizeButton = false, HasMaximizeButton = false };
            var panel = new StackPanel { Margin = new Thickness(12) };
            panel.Children.Add(new TextBlock { Text = prompt, TextWrapping = TextWrapping.Wrap, Margin = new Thickness(0, 0, 0, 6) });
            var text = new TextBox();
            var password = new PasswordBox();
            panel.Children.Add(secret ? (UIElement)password : text);
            var error = new TextBlock { Foreground = System.Windows.Media.Brushes.IndianRed, TextWrapping = TextWrapping.Wrap };
            panel.Children.Add(error);
            var buttons = Buttons(window, out var ok);
            panel.Children.Add(buttons);
            string Value() => secret ? password.Password : text.Text;
            ok.Click += (_, __) =>
            {
                var problem = validate?.Invoke(Value());
                if (problem != null) error.Text = problem;
                else window.DialogResult = true;
            };
            window.Content = panel;
            window.Loaded += (_, __) => (secret ? (UIElement)password : text).Focus();
            return window.ShowModal() == true ? Value() : null;
        }

        private static StackPanel Buttons(Window window, out Button ok)
        {
            var row = new StackPanel { Orientation = Orientation.Horizontal, HorizontalAlignment = HorizontalAlignment.Right, Margin = new Thickness(0, 8, 0, 0) };
            ok = new Button { Content = "OK", IsDefault = true, MinWidth = 80, Margin = new Thickness(0, 0, 6, 0) };
            var cancel = new Button { Content = "Cancel", IsCancel = true, MinWidth = 80 };
            row.Children.Add(ok);
            row.Children.Add(cancel);
            return row;
        }

        // -- /rewind (H11) -----------------------------------------------------------------------

        public static void PickRewind(IReadOnlyList<Protocol.RewindPoint> points, Action<RewindChoice> chosen)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            var newestFirst = points.Reverse().ToList();
            var labels = newestFirst.Select(p =>
                (string.IsNullOrEmpty(p.Text) ? "(empty message)" : System.Text.RegularExpressions.Regex.Replace(p.Text, @"\s+", " "))
                + (p.FilesChanged > 0 ? "  —  " + p.FilesChanged + " file(s) changed since" : "")).ToList();
            var which = Pick(Brand.Product + ": Rewind", "Rewind to before which message?", labels);
            if (which == null) return;
            var point = newestFirst[which.Value];
            var what = Choose(Brand.Product + ": Rewind", "What should go back? (Changes made by Bash commands are not undone.)",
                "Code and conversation", "Conversation only", "Code only");
            if (what == null) return;
            var code = what != 1;
            var conversation = what != 2;
            var outside = false;
            if (code && point.OutsideFiles.Count > 0)
            {
                var ask = Choose(Brand.Product + ": Rewind",
                    point.OutsideFiles.Count + " changed file(s) are outside the solution. Restore them too?\n\n"
                        + string.Join("\n", point.OutsideFiles.Take(10)),
                    "Restore them too", "Only the solution's files");
                if (ask == null) return;
                outside = ask == 0;
            }
            chosen(new RewindChoice(point.Turn, code, conversation, outside));
        }

        // -- the @ button (H13) ------------------------------------------------------------------

        private static readonly HashSet<string> Skipped = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { ".git", ".vs", "bin", "obj", "node_modules", ".venv", "__pycache__", "packages" };

        public static void AttachFile(string? folder, Action<string> chosen)
        {
            ThreadHelper.ThrowIfNotOnUIThread();
            if (folder == null) return;
            var files = new List<string>();
            var stack = new Stack<string>(new[] { folder });
            while (stack.Count > 0 && files.Count < 5000)
            {
                var dir = stack.Pop();
                try
                {
                    foreach (var d in Directory.GetDirectories(dir))
                        if (!Skipped.Contains(Path.GetFileName(d))) stack.Push(d);
                    foreach (var f in Directory.GetFiles(dir)) files.Add(f.Substring(folder.Length).TrimStart('\\', '/').Replace('\\', '/'));
                }
                catch (UnauthorizedAccessException) { }
                catch (IOException) { }
            }
            files.Sort(StringComparer.OrdinalIgnoreCase);
            var pick = Pick(Brand.Product + ": Attach a file (@)", "Type to narrow the list:", files, filter: true);
            if (pick != null) chosen(files[pick.Value]);
        }
    }
}
