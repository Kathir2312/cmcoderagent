package cmcoder.ide.core;

import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * A small JSON reader and writer (RFC 8259), so the core needs no library that
 * could clash with the IDE's own. Objects become {@code Map<String, Object>}
 * (keys in order), arrays {@code List<Object>}, numbers {@code Long} or
 * {@code Double}, plus {@code String}, {@code Boolean} and {@code null}.
 */
public final class Json {
    /** Deeper nesting than this is refused (no stack overflow on hostile input). */
    public static final int MAX_DEPTH = 256;

    private Json() {}

    public static final class ParseException extends RuntimeException {
        private static final long serialVersionUID = 1L;

        public ParseException(String message) {
            super(message);
        }
    }

    // -- reading ------------------------------------------------------------------

    public static Object parse(String text) {
        Reader r = new Reader(text);
        r.space();
        Object value = r.value(0);
        r.space();
        if (r.pos != text.length()) throw r.error("unexpected text after the value");
        return value;
    }

    /** Parses a JSON object, or returns null if the text isn't one. */
    @SuppressWarnings("unchecked")
    public static Map<String, Object> parseObject(String text) {
        try {
            Object value = parse(text);
            return value instanceof Map ? (Map<String, Object>) value : null;
        } catch (ParseException e) {
            return null;
        }
    }

    private static final class Reader {
        final String s;
        int pos;

        Reader(String s) {
            this.s = s;
        }

        ParseException error(String what) {
            return new ParseException(what + " at character " + pos);
        }

        void space() {
            while (pos < s.length()) {
                char c = s.charAt(pos);
                if (c != ' ' && c != '\t' && c != '\n' && c != '\r') return;
                pos++;
            }
        }

        Object value(int depth) {
            if (depth > MAX_DEPTH) throw error("nested too deeply");
            if (pos >= s.length()) throw error("missing value");
            char c = s.charAt(pos);
            switch (c) {
                case '{':
                    return object(depth);
                case '[':
                    return array(depth);
                case '"':
                    return string();
                case 't':
                    return word("true", Boolean.TRUE);
                case 'f':
                    return word("false", Boolean.FALSE);
                case 'n':
                    return word("null", null);
                default:
                    if (c == '-' || (c >= '0' && c <= '9')) return number();
                    throw error("unexpected '" + c + "'");
            }
        }

        Object word(String w, Object value) {
            if (!s.startsWith(w, pos)) throw error("unexpected text");
            pos += w.length();
            return value;
        }

        Map<String, Object> object(int depth) {
            Map<String, Object> map = new LinkedHashMap<>();
            pos++; // {
            space();
            if (peek() == '}') {
                pos++;
                return map;
            }
            while (true) {
                space();
                if (peek() != '"') throw error("expected a key");
                String key = string();
                space();
                expect(':');
                space();
                map.put(key, value(depth + 1));
                space();
                char c = next();
                if (c == '}') return map;
                if (c != ',') throw error("expected ',' or '}'");
            }
        }

        List<Object> array(int depth) {
            List<Object> list = new ArrayList<>();
            pos++; // [
            space();
            if (peek() == ']') {
                pos++;
                return list;
            }
            while (true) {
                space();
                list.add(value(depth + 1));
                space();
                char c = next();
                if (c == ']') return list;
                if (c != ',') throw error("expected ',' or ']'");
            }
        }

        String string() {
            pos++; // "
            StringBuilder b = new StringBuilder();
            while (true) {
                if (pos >= s.length()) throw error("unterminated string");
                char c = s.charAt(pos++);
                if (c == '"') return b.toString();
                if (c < 0x20) throw error("control character in a string");
                if (c != '\\') {
                    b.append(c);
                    continue;
                }
                if (pos >= s.length()) throw error("unterminated escape");
                char e = s.charAt(pos++);
                switch (e) {
                    case '"': b.append('"'); break;
                    case '\\': b.append('\\'); break;
                    case '/': b.append('/'); break;
                    case 'b': b.append('\b'); break;
                    case 'f': b.append('\f'); break;
                    case 'n': b.append('\n'); break;
                    case 'r': b.append('\r'); break;
                    case 't': b.append('\t'); break;
                    case 'u':
                        if (pos + 4 > s.length()) throw error("short \\u escape");
                        try {
                            b.append((char) Integer.parseInt(s.substring(pos, pos + 4), 16));
                        } catch (NumberFormatException x) {
                            throw error("bad \\u escape");
                        }
                        pos += 4;
                        break;
                    default:
                        throw error("bad escape \\" + e);
                }
            }
        }

        Object number() {
            int start = pos;
            if (peek() == '-') pos++;
            if (peek() == '0') {
                pos++;
            } else if (Character.isDigit(peek())) {
                while (Character.isDigit(peek())) pos++;
            } else {
                throw error("bad number");
            }
            boolean fraction = false;
            if (peek() == '.') {
                fraction = true;
                pos++;
                if (!Character.isDigit(peek())) throw error("bad number");
                while (Character.isDigit(peek())) pos++;
            }
            if (peek() == 'e' || peek() == 'E') {
                fraction = true;
                pos++;
                if (peek() == '+' || peek() == '-') pos++;
                if (!Character.isDigit(peek())) throw error("bad number");
                while (Character.isDigit(peek())) pos++;
            }
            String text = s.substring(start, pos);
            if (!fraction) {
                try {
                    return Long.parseLong(text);
                } catch (NumberFormatException tooBig) {
                    // falls through to a double
                }
            }
            return Double.parseDouble(text);
        }

        char peek() {
            return pos < s.length() ? s.charAt(pos) : '\0';
        }

        char next() {
            if (pos >= s.length()) throw error("unexpected end");
            return s.charAt(pos++);
        }

        void expect(char c) {
            if (next() != c) {
                pos--;
                throw error("expected '" + c + "'");
            }
        }
    }

    // -- writing ------------------------------------------------------------------

    /** JSON text for a value built from maps, lists, strings, numbers, booleans and null. */
    public static String write(Object value) {
        StringBuilder b = new StringBuilder();
        write(b, value, 0);
        return b.toString();
    }

    private static void write(StringBuilder b, Object v, int depth) {
        if (depth > MAX_DEPTH) throw new IllegalArgumentException("nested too deeply");
        if (v == null) {
            b.append("null");
        } else if (v instanceof String) {
            quote(b, (String) v);
        } else if (v instanceof Boolean) {
            b.append(v.toString());
        } else if (v instanceof Integer || v instanceof Long || v instanceof Short || v instanceof Byte) {
            b.append(v.toString());
        } else if (v instanceof Double || v instanceof Float) {
            double d = ((Number) v).doubleValue();
            if (Double.isNaN(d) || Double.isInfinite(d)) throw new IllegalArgumentException("not a JSON number: " + d);
            b.append(d == Math.rint(d) && Math.abs(d) < 1e15 ? Long.toString((long) d) : Double.toString(d));
        } else if (v instanceof Map) {
            b.append('{');
            boolean first = true;
            for (Map.Entry<?, ?> e : ((Map<?, ?>) v).entrySet()) {
                if (!first) b.append(',');
                first = false;
                quote(b, String.valueOf(e.getKey()));
                b.append(':');
                write(b, e.getValue(), depth + 1);
            }
            b.append('}');
        } else if (v instanceof Iterable) {
            b.append('[');
            boolean first = true;
            for (Object item : (Iterable<?>) v) {
                if (!first) b.append(',');
                first = false;
                write(b, item, depth + 1);
            }
            b.append(']');
        } else if (v instanceof Raw) {
            b.append(((Raw) v).json);
        } else {
            throw new IllegalArgumentException("can't write " + v.getClass().getName() + " as JSON");
        }
    }

    private static void quote(StringBuilder b, String s) {
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '"': b.append("\\\""); break;
                case '\\': b.append("\\\\"); break;
                case '\n': b.append("\\n"); break;
                case '\r': b.append("\\r"); break;
                case '\t': b.append("\\t"); break;
                case '\b': b.append("\\b"); break;
                case '\f': b.append("\\f"); break;
                default:
                    // Also U+2028/2029: valid JSON, but not inside a JavaScript
                    // string, and this text is handed to the panel's page.
                    if (c < 0x20 || c == ' ' || c == ' ') {
                        b.append(String.format("\\u%04x", (int) c));
                    } else {
                        b.append(c);
                    }
            }
        }
        b.append('"');
    }

    /** JSON text already checked elsewhere, written as it is (e.g. an event relayed to the panel). */
    public static final class Raw {
        final String json;

        public Raw(String json) {
            this.json = json;
        }
    }

    // -- reading helpers ----------------------------------------------------------

    public static String string(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        return v instanceof String ? (String) v : null;
    }

    public static boolean bool(Map<String, Object> o, String key) {
        return o != null && Boolean.TRUE.equals(o.get(key));
    }

    public static long number(Map<String, Object> o, String key, long otherwise) {
        Object v = o == null ? null : o.get(key);
        return v instanceof Number ? ((Number) v).longValue() : otherwise;
    }

    @SuppressWarnings("unchecked")
    public static Map<String, Object> object(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        return v instanceof Map ? (Map<String, Object>) v : null;
    }

    public static List<Object> list(Map<String, Object> o, String key) {
        Object v = o == null ? null : o.get(key);
        if (v instanceof List) {
            @SuppressWarnings("unchecked")
            List<Object> l = (List<Object>) v;
            return l;
        }
        return Collections.emptyList();
    }
}
