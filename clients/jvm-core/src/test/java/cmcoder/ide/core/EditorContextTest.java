package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

/** The same rules and labels as the VS Code extension's editorContext.ts. */
class EditorContextTest {
    @Test
    void onlyErrorsAndWarningsAtMostThirty() {
        List<Map<String, Object>> diags = new ArrayList<>();
        for (int i = 0; i < 40; i++) diags.add(EditorContext.diagnostic("/p/a.py", i + 1, "error", "e" + i, null));
        diags.add(0, EditorContext.diagnostic("/p/a.py", 1, "info", "just info", null));
        diags.add(1, EditorContext.diagnostic("/p/a.py", 1, "hint", "a hint", null));
        Map<String, Object> c = EditorContext.of("/p/a.py", null, diags);
        assertEquals(EditorContext.MAX_DIAGNOSTICS, Json.list(c, "diagnostics").size());
        assertEquals("e0", Json.string(cast(Json.list(c, "diagnostics").get(0)), "message"));
        assertNull(EditorContext.of(null, null, diags));
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> cast(Object o) {
        return (Map<String, Object>) o;
    }

    @Test
    void labels() {
        assertNull(EditorContext.label(null));
        assertEquals("app.py", EditorContext.label(EditorContext.of("/p/app.py", null, List.of())));
        assertEquals("app.py:10-14 · 2 problems", EditorContext.label(EditorContext.of("/p/app.py",
                EditorContext.selection("/p/app.py", 10, 14, "x"),
                List.of(EditorContext.diagnostic("/p/app.py", 1, "error", "a", null),
                        EditorContext.diagnostic("/p/app.py", 2, "warning", "b", "lint")))));
        assertEquals("app.py:3 · 1 problem", EditorContext.label(EditorContext.of("/p/app.py",
                EditorContext.selection("/p/app.py", 3, 3, "x"),
                List.of(EditorContext.diagnostic("/p/app.py", 1, "error", "a", null)))));
    }

    @Test
    void aSelectionEndingAtColumnZeroLeavesThatLineOut() {
        // Lines from 0 in, from 1 out: lines 3-5 selected up to the start of line 6.
        assertEquals(5, EditorContext.endLine(2, 5, 0));
        assertEquals(6, EditorContext.endLine(2, 5, 4));
        assertEquals(3, EditorContext.endLine(2, 2, 0)); // an empty-ish selection on one line keeps it
    }
}
