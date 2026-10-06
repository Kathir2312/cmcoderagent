using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using System.Collections.Generic;

namespace Cmcoder.Core
{
    /// <summary>Reading fields of cmcoder's JSON without exceptions for missing or mistyped ones.</summary>
    public static class Json
    {
        /// <summary>A JSON object, or null if the text isn't one.</summary>
        public static JObject? ParseObject(string text)
        {
            try
            {
                using var reader = new JsonTextReader(new System.IO.StringReader(text)) { DateParseHandling = DateParseHandling.None, MaxDepth = 256 };
                var token = JToken.ReadFrom(reader);
                return token as JObject;
            }
            catch (JsonException)
            {
                return null;
            }
        }

        public static string? Str(JObject? o, string key) =>
            o?[key] is JValue v && v.Type == JTokenType.String ? (string?)v.Value : null;

        public static bool Bool(JObject? o, string key) =>
            o?[key] is JValue v && v.Type == JTokenType.Boolean && (bool)v.Value!;

        public static long Num(JObject? o, string key, long otherwise) =>
            o?[key] is JValue v && (v.Type == JTokenType.Integer || v.Type == JTokenType.Float) ? System.Convert.ToInt64(v.Value) : otherwise;

        public static JObject? Obj(JObject? o, string key) => o?[key] as JObject;

        public static IReadOnlyList<JToken> List(JObject? o, string key) =>
            o?[key] is JArray a ? new List<JToken>(a) : new List<JToken>();

        public static List<string> Strings(JObject? o, string key)
        {
            var list = new List<string>();
            foreach (var t in List(o, key))
                if (t.Type == JTokenType.String) list.Add((string)t!);
            return list;
        }

        /// <summary>Compact JSON text.</summary>
        public static string Write(object? value) => JsonConvert.SerializeObject(value, Formatting.None);
    }
}
