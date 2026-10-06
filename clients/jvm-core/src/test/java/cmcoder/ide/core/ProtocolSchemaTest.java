package cmcoder.ide.core;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.junit.jupiter.api.Assertions.fail;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.TimeUnit;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * The protocol classes against cmcoder's own schema ({@code cmcoder
 * protocol-schema}): every field this side reads exists with that type, and
 * every message it builds is one cmcoder accepts. A protocol change the IDE
 * side doesn't follow fails here.
 */
class ProtocolSchemaTest {
    private static Map<String, Object> events;
    private static Map<String, Object> messages;

    @BeforeAll
    static void schema() throws IOException, InterruptedException {
        Process p = new ProcessBuilder(Fixtures.program().toString(), "protocol-schema").start();
        String text = new String(p.getInputStream().readAllBytes(), StandardCharsets.UTF_8);
        assertTrue(p.waitFor(60, TimeUnit.SECONDS));
        assertEquals(0, p.exitValue(), "cmcoder protocol-schema failed");
        Map<String, Object> schema = Json.parseObject(text);
        events = Json.object(Json.object(schema, "events"), "$defs");
        messages = Json.object(Json.object(schema, "messages"), "$defs");
        assertNotNull(events);
        assertNotNull(messages);
    }

    /** The definition for a type, e.g. "system_init" in events. */
    private static Map<String, Object> definition(Map<String, Object> defs, String type) {
        for (Object d : defs.values()) {
            @SuppressWarnings("unchecked")
            Map<String, Object> def = (Map<String, Object>) d;
            Map<String, Object> t = Json.object(Json.object(def, "properties"), "type");
            if (type.equals(Json.string(t, "const"))) return def;
        }
        return fail("no definition for " + type);
    }

    /** The JSON types a property allows ("string", "object", "null", ...). */
    private static List<String> types(Map<String, Object> property) {
        java.util.ArrayList<String> out = new java.util.ArrayList<>();
        if (Json.string(property, "type") != null) out.add(Json.string(property, "type"));
        if (property.containsKey("$ref")) out.add("object");
        for (Object alt : Json.list(property, "anyOf")) {
            @SuppressWarnings("unchecked")
            Map<String, Object> a = (Map<String, Object>) alt;
            out.addAll(types(a));
        }
        return out;
    }

    @Test
    void everyFieldReadExistsWithThatType() {
        for (Protocol.Read read : Protocol.READS) {
            Map<String, Object> props = Json.object(definition(events, read.event), "properties");
            Map<String, Object> property = Json.object(props, read.field);
            assertNotNull(property, read.event + "." + read.field + " isn't in the protocol");
            assertTrue(types(property).contains(read.jsonType),
                    read.event + "." + read.field + " is " + types(property) + ", not " + read.jsonType);
        }
    }

    @Test
    void protocolVersionMatches() {
        Map<String, Object> version = Json.object(Json.object(definition(events, "system_init"), "properties"), "protocol_version");
        assertEquals((long) Protocol.VERSION, Json.number(version, "default", -1));
    }

    @Test
    void everyMessageBuiltIsOneCmcoderAccepts() {
        for (String json : Protocol.samples()) {
            Map<String, Object> m = Json.parseObject(json);
            assertNotNull(m, json);
            validate(m, definition(messages, Json.string(m, "type")), json);
        }
    }

    /** {@code value} against a schema node (properties, $ref, anyOf, items, const, enum); nested too. */
    @SuppressWarnings("unchecked")
    private static void validate(Object value, Map<String, Object> node, String where) {
        List<Object> anyOf = Json.list(node, "anyOf");
        if (!anyOf.isEmpty()) {
            for (Object alt : anyOf) {
                try {
                    validate(value, (Map<String, Object>) alt, where);
                    return;
                } catch (AssertionError tryNext) {
                    // the next alternative
                }
            }
            fail(where + ": matches none of " + anyOf);
        }
        String ref = Json.string(node, "$ref");
        if (ref != null) {
            validate(value, Json.object(messages, ref.substring(ref.lastIndexOf('/') + 1)), where);
            return;
        }
        if (node.containsKey("const")) assertEquals(node.get("const"), value, where);
        if (node.containsKey("enum")) assertTrue(Json.list(node, "enum").contains(value), where + ": " + value);
        String type = Json.string(node, "type");
        if (type != null) {
            String actual = jsonType(value);
            assertTrue(type.equals(actual) || (type.equals("number") && actual.equals("integer")),
                    where + ": " + value + " is " + actual + ", should be " + type);
        }
        if ("object".equals(type) && node.containsKey("properties")) {
            Map<String, Object> object = (Map<String, Object>) value;
            Map<String, Object> props = Json.object(node, "properties");
            for (Object required : Json.list(node, "required")) {
                assertTrue(object.containsKey((String) required), where + " lacks " + required);
            }
            for (Map.Entry<String, Object> field : object.entrySet()) {
                Map<String, Object> property = Json.object(props, field.getKey());
                assertNotNull(property, where + ": cmcoder doesn't know " + field.getKey());
                validate(field.getValue(), property, where + "." + field.getKey());
            }
        }
        if ("array".equals(type) && node.containsKey("items")) {
            for (Object item : (List<Object>) value) validate(item, Json.object(node, "items"), where + "[]");
        }
    }

    private static String jsonType(Object v) {
        if (v == null) return "null";
        if (v instanceof String) return "string";
        if (v instanceof Boolean) return "boolean";
        if (v instanceof Long) return "integer";
        if (v instanceof Number) return "number";
        if (v instanceof List) return "array";
        return "object";
    }
}
