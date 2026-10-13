using Cmcoder.Core;
using Newtonsoft.Json.Linq;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using Xunit;

namespace Cmcoder.Core.Tests
{
    /// <summary>
    /// The protocol classes against cmcoder's own schema (<c>cmcoder protocol-schema</c>):
    /// every field read exists with that type, every message built is one cmcoder accepts.
    /// </summary>
    public class ProtocolSchemaTests
    {
        private static (JObject events, JObject messages) Schema()
        {
            var psi = new ProcessStartInfo(Fixtures.Program(), "protocol-schema") { UseShellExecute = false, RedirectStandardOutput = true };
            using var p = Process.Start(psi)!;
            var text = p.StandardOutput.ReadToEnd();
            p.WaitForExit();
            Assert.Equal(0, p.ExitCode);
            var schema = Json.ParseObject(text)!;
            return (Json.Obj(Json.Obj(schema, "events"), "$defs")!, Json.Obj(Json.Obj(schema, "messages"), "$defs")!);
        }

        private static JObject Definition(JObject defs, string type)
        {
            foreach (var p in defs.Properties())
            {
                var def = (JObject)p.Value;
                if (Json.Str(Json.Obj(Json.Obj(def, "properties"), "type"), "const") == type) return def;
            }
            throw new Xunit.Sdk.XunitException("no definition for " + type);
        }

        private static List<string> Types(JObject property)
        {
            var list = new List<string>();
            var t = Json.Str(property, "type");
            if (t != null) list.Add(t);
            if (property["$ref"] != null) list.Add("object");
            foreach (var alt in Json.List(property, "anyOf")) list.AddRange(Types((JObject)alt));
            return list;
        }

        [SkippableFact]
        public void EveryFieldReadExistsWithThatType()
        {
            var (events, _) = Schema();
            foreach (var read in Protocol.Reads)
            {
                var property = Json.Obj(Json.Obj(Definition(events, read.Event), "properties"), read.Field);
                Assert.True(property != null, read.Event + "." + read.Field + " isn't in the protocol");
                Assert.Contains(read.JsonType, Types(property!));
            }
        }

        [SkippableFact]
        public void ProtocolVersionMatches()
        {
            var (events, _) = Schema();
            var version = Json.Obj(Json.Obj(Definition(events, "system_init"), "properties"), "protocol_version");
            Assert.Equal(Protocol.Version, Json.Num(version, "default", -1));
        }

        [SkippableFact]
        public void EveryMessageBuiltIsOneCmcoderAccepts()
        {
            var (_, messages) = Schema();
            foreach (var json in Protocol.Samples())
            {
                var m = Json.ParseObject(json)!;
                Validate(m, Definition(messages, Json.Str(m, "type")!), messages, json);
            }
        }

        private static void Validate(JToken value, JObject node, JObject defs, string where)
        {
            var anyOf = Json.List(node, "anyOf");
            if (anyOf.Count > 0)
            {
                foreach (var alt in anyOf)
                {
                    try
                    {
                        Validate(value, (JObject)alt, defs, where);
                        return;
                    }
                    catch (Xunit.Sdk.XunitException) { /* the next one */ }
                }
                throw new Xunit.Sdk.XunitException(where + ": matches none of " + node["anyOf"]);
            }
            var r = Json.Str(node, "$ref");
            if (r != null)
            {
                Validate(value, (JObject)defs[r.Substring(r.LastIndexOf('/') + 1)]!, defs, where);
                return;
            }
            if (node["const"] != null) Assert.True(JToken.DeepEquals(node["const"], value), where);
            if (node["enum"] != null) Assert.True(Json.List(node, "enum").Any(e => JToken.DeepEquals(e, value)), where + ": " + value);
            var type = Json.Str(node, "type");
            if (type != null)
            {
                var actual = JsonType(value);
                Assert.True(type == actual || (type == "number" && actual == "integer"), where + ": " + value + " is " + actual + ", should be " + type);
            }
            if (type == "object" && node["properties"] is JObject props)
            {
                var o = (JObject)value;
                foreach (var req in Json.List(node, "required")) Assert.True(o[(string)req!] != null, where + " lacks " + req);
                foreach (var f in o.Properties())
                {
                    var property = Json.Obj(props, f.Name);
                    Assert.True(property != null, where + ": cmcoder doesn't know " + f.Name);
                    Validate(f.Value, property!, defs, where + "." + f.Name);
                }
            }
            if (type == "array" && node["items"] is JObject items)
                foreach (var item in (JArray)value) Validate(item, items, defs, where + "[]");
        }

        private static string JsonType(JToken v) => v.Type switch
        {
            JTokenType.Null => "null",
            JTokenType.String => "string",
            JTokenType.Boolean => "boolean",
            JTokenType.Integer => "integer",
            JTokenType.Float => "number",
            JTokenType.Array => "array",
            _ => "object",
        };
    }
}
