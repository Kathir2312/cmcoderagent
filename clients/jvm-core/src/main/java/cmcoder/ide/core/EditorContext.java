package cmcoder.ide.core;

import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * What the user has open in the editor, sent with a message (host duty H7):
 * the active file, the selection (lines from 1, end line inclusive) and its
 * errors and warnings (at most {@link #MAX_DIAGNOSTICS}). The same rules as
 * the VS Code extension's editorContext.ts.
 */
public final class EditorContext {
    public static final int MAX_DIAGNOSTICS = 30;

    private EditorContext() {}

    public static Map<String, Object> selection(String path, int startLine, int endLine, String text) {
        Map<String, Object> s = new LinkedHashMap<>();
        s.put("path", path);
        s.put("start_line", startLine);
        s.put("end_line", Math.max(startLine, endLine));
        s.put("text", text);
        return s;
    }

    /** severity: "error", "warning", "info" or "hint". */
    public static Map<String, Object> diagnostic(String path, int line, String severity, String message, String source) {
        Map<String, Object> d = new LinkedHashMap<>();
        d.put("path", path);
        d.put("line", line);
        d.put("severity", severity);
        d.put("message", message);
        d.put("source", source);
        return d;
    }

    /**
     * The context for a message: null when no file is open. Only errors and
     * warnings are sent, at most {@link #MAX_DIAGNOSTICS}.
     */
    public static Map<String, Object> of(String activeFile, Map<String, Object> selection, List<Map<String, Object>> diagnostics) {
        if (activeFile == null) return null;
        List<Map<String, Object>> kept = new ArrayList<>();
        for (Map<String, Object> d : diagnostics) {
            String severity = Json.string(d, "severity");
            if (("error".equals(severity) || "warning".equals(severity)) && kept.size() < MAX_DIAGNOSTICS) kept.add(d);
        }
        Map<String, Object> c = new LinkedHashMap<>();
        c.put("active_file", activeFile);
        c.put("selection", selection);
        c.put("diagnostics", kept);
        return c;
    }

    /**
     * A selection's last line, from 1. A selection that ends at the start of a
     * line (column 0) doesn't include that line.
     */
    public static int endLine(int startLine0, int endLine0, int endColumn0) {
        return endColumn0 == 0 && endLine0 > startLine0 ? endLine0 : endLine0 + 1;
    }

    /** The chat's short label, e.g. "app.py:10-14 · 2 problems"; null without a file. */
    public static String label(Map<String, Object> context) {
        String file = Json.string(context, "active_file");
        if (file == null) return null;
        StringBuilder label = new StringBuilder(Paths.get(file).getFileName().toString());
        Map<String, Object> sel = Json.object(context, "selection");
        if (sel != null) {
            long start = Json.number(sel, "start_line", 0);
            long end = Json.number(sel, "end_line", start);
            label.append(':').append(start);
            if (end > start) label.append('-').append(end);
        }
        int n = Json.list(context, "diagnostics").size();
        if (n > 0) label.append(" · ").append(n).append(n == 1 ? " problem" : " problems");
        return label.toString();
    }
}
