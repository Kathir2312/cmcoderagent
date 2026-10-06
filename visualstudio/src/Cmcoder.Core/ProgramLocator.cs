using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.InteropServices;

namespace Cmcoder.Core
{
    /// <summary>
    /// Which cmcoder program to start (H1): the user's own setting, else the copy
    /// inside the extension, else <c>cmcoder</c> on PATH. Never Python or uv
    /// (developers' PCs may have neither).
    ///
    /// A project must not be able to choose the program: the setting is the
    /// user's (never a project file) and must be a full path, and PATH is
    /// searched without its relative entries and without the project folder (a
    /// cloned repository could ship its own cmcoder.exe). Batch files are never
    /// started (cmd.exe re-parses their arguments).
    /// </summary>
    public static class ProgramLocator
    {
        public static bool Windows => RuntimeInformation.IsOSPlatform(OSPlatform.Windows);

        /// <summary>Where the extension keeps its copy: <c>&lt;extensionDir&gt;/bin/cmcoder/cmcoder[.exe]</c>.</summary>
        public static string Bundled(string extensionDir) =>
            Path.Combine(extensionDir, "bin", "cmcoder", Windows ? "cmcoder.exe" : "cmcoder");

        /// <summary>The result of a lookup: the program, or why there is none.</summary>
        public sealed class Found
        {
            internal Found(string? program, string? problem)
            {
                Program = program;
                Problem = problem;
            }

            public string? Program { get; }
            public string? Problem { get; }
        }

        public static Found Find(string? setting, string? extensionDir, string? projectDir, IDictionary<string, string?> env)
        {
            if (!string.IsNullOrWhiteSpace(setting))
            {
                var p = setting!.Trim();
                if (!Path.IsPathRooted(p) || (Windows && !IsFullWindowsPath(p)))
                    return new Found(null, "The cmcoder program setting must be a full path (it is \"" + p + "\").");
                if (Batch(p))
                    return new Found(null, "The cmcoder program setting points to a batch file; set it to cmcoder.exe itself.");
                return File.Exists(p)
                    ? new Found(p, null)
                    : new Found(null, "The cmcoder program set in the settings doesn't exist: " + p);
            }
            if (extensionDir != null)
            {
                var b = Bundled(extensionDir);
                if (File.Exists(b)) return new Found(b, null);
            }
            var onPath = OnPath("cmcoder", projectDir, env);
            if (onPath != null) return new Found(onPath, null);
            return new Found(null, "cmcoder wasn't found: this extension should contain it. Install the extension file "
                + "again, or set the cmcoder program's full path in Tools > Options > cmcoder.");
        }

        /// <summary>"C:\x" or "\\server\share\x"; not "\x" (relative to the current drive).</summary>
        private static bool IsFullWindowsPath(string p) =>
            (p.Length >= 3 && char.IsLetter(p[0]) && p[1] == ':' && (p[2] == '\\' || p[2] == '/')) || p.StartsWith(@"\\", StringComparison.Ordinal);

        /// <summary>A program on PATH, skipping relative entries and the project folder; null if none.</summary>
        public static string? OnPath(string name, string? projectDir, IDictionary<string, string?> env)
        {
            string? path = null;
            foreach (var e in env)
            {
                if (string.Equals(e.Key, "PATH", StringComparison.OrdinalIgnoreCase))
                {
                    path = e.Value;
                    break;
                }
            }
            if (path == null) return null;
            var here = projectDir == null ? null : Normalize(projectDir);
            var exts = new List<string>();
            if (Windows)
            {
                env.TryGetValue("PATHEXT", out var pathext);
                var parts = (pathext ?? ".COM;.EXE;.BAT;.CMD").Split(';');
                var named = false;
                foreach (var e in parts)
                    if (e.Length > 0 && name.EndsWith(e, StringComparison.OrdinalIgnoreCase)) named = true;
                if (named) exts.Add("");
                else foreach (var e in parts) if (e.Length > 0) exts.Add(e.ToLowerInvariant());
            }
            else
            {
                exts.Add("");
            }
            foreach (var dir in path.Split(Path.PathSeparator))
            {
                if (dir.Length == 0 || !Path.IsPathRooted(dir)) continue;
                if (Windows && !IsFullWindowsPath(dir)) continue;
                var norm = Normalize(dir);
                if (here != null && string.Equals(norm, here, Windows ? StringComparison.OrdinalIgnoreCase : StringComparison.Ordinal)) continue;
                foreach (var ext in exts)
                {
                    if (Batch(name + ext)) continue;
                    var candidate = Path.Combine(dir, name + ext);
                    if (File.Exists(candidate) && (Windows || Executable(candidate))) return candidate;
                }
            }
            return null;
        }

        private static string Normalize(string p) => Path.GetFullPath(p).TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar);

        internal static bool Batch(string file) =>
            file.EndsWith(".bat", StringComparison.OrdinalIgnoreCase) || file.EndsWith(".cmd", StringComparison.OrdinalIgnoreCase);

        private static bool Executable(string file)
        {
#if NET
            if (OperatingSystem.IsWindows()) return true;
            return (File.GetUnixFileMode(file) & (UnixFileMode.UserExecute | UnixFileMode.GroupExecute | UnixFileMode.OtherExecute)) != 0;
#else
            return true; // only Windows runs this build
#endif
        }
    }
}
