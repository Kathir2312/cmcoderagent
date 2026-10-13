package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

class JsonTest {
    @Test
    void readsEveryKindOfValue() {
        Map<String, Object> o = Json.parseObject(
                " {\"s\":\"a\\\"b\\\\c\\/\\n\\t\\u00e4\", \"i\":-42, \"big\":123456789012345678901, \"f\":1.5e2,"
                        + " \"t\":true, \"n\":null, \"a\":[1,[],{}], \"o\":{\"k\":\"v\"}} ");
        assertEquals("a\"b\\c/\n\tä", o.get("s"));
        assertEquals(-42L, o.get("i"));
        assertEquals(1.2345678901234568E20, o.get("big")); // too big for a long: a double
        assertEquals(150.0, o.get("f"));
        assertEquals(Boolean.TRUE, o.get("t"));
        assertTrue(o.containsKey("n") && o.get("n") == null);
        assertEquals(Arrays.asList(1L, List.of(), Map.of()), o.get("a"));
        assertEquals(Map.of("k", "v"), o.get("o"));
    }

    @Test
    void keepsTextOutsideTheBasicPlaneAndKeyOrder() {
        String text = "émoji 😀, 中文, Ünal";
        String json = Json.write(Map.of("t", text));
        assertEquals(text, Json.parseObject(json).get("t"));
        assertEquals("😀", Json.parse("\"\\ud83d\\ude00\""));
        Map<String, Object> ordered = new LinkedHashMap<>();
        ordered.put("type", "x");
        ordered.put("a", 1);
        ordered.put("b", 2);
        assertEquals("{\"type\":\"x\",\"a\":1,\"b\":2}", Json.write(ordered));
    }

    @Test
    void escapesWhatJavaScriptCantTake() {
        // Control characters, and U+2028/2029 (fine in JSON, not in a JavaScript string).
        assertEquals("\"a\\u0001b\\u2028c\\u2029\\n\"", Json.write("a\u0001b\u2028c\u2029\n"));
    }

    @Test
    void writesNumbersAndRaw() {
        assertEquals("[1,2.5,-3,true,null]", Json.write(Arrays.asList(1, 2.5, -3L, true, null)));
        assertEquals("{\"event\":{\"type\":\"x\"}}", Json.write(Map.of("event", new Json.Raw("{\"type\":\"x\"}"))));
        assertThrows(IllegalArgumentException.class, () -> Json.write(Double.NaN));
        assertThrows(IllegalArgumentException.class, () -> Json.write(new Object()));
    }

    @Test
    void refusesWhatIsntJson() {
        for (String bad : new String[] {
            "", "{", "{\"a\":1,}", "[1,]", "{\"a\" 1}", "{a:1}", "\"open", "\"bad \\x escape\"", "01", "1.", "-",
            "tru", "{} {}", "\"tab\there\"", "\"\\u12\"", "NaN",
        }) {
            assertThrows(Json.ParseException.class, () -> Json.parse(bad), bad);
            assertNull(Json.parseObject(bad), bad);
        }
        assertNull(Json.parseObject("[1]")); // valid JSON, not an object
    }

    @Test
    void refusesDeepNestingInsteadOfOverflowing() {
        String deep = "[".repeat(100_000) + "]".repeat(100_000);
        assertThrows(Json.ParseException.class, () -> Json.parse(deep));
        String fine = "[".repeat(Json.MAX_DEPTH) + "]".repeat(Json.MAX_DEPTH);
        Json.parse(fine);
    }

    @Test
    void helpersTolerateMissingAndWrongTypes() {
        Map<String, Object> o = Json.parseObject("{\"s\":1,\"n\":\"x\",\"l\":{}}");
        assertNull(Json.string(o, "s"));
        assertEquals(7, Json.number(o, "n", 7));
        assertTrue(Json.list(o, "l").isEmpty());
        assertNull(Json.object(null, "x"));
    }
}
