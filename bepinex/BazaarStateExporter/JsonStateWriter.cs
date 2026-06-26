using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;

namespace BazaarStateExporter
{
    public static class JsonStateWriter
    {
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);

        public static void WriteAtomic(string path, GameStateSnapshot snapshot)
        {
            string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
            string directory = Path.GetDirectoryName(fullPath);
            if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
            {
                Directory.CreateDirectory(directory);
            }

            string tempPath = fullPath + ".tmp";
            File.WriteAllText(tempPath, ToJson(snapshot), Utf8NoBom);

            if (File.Exists(fullPath))
            {
                File.Replace(tempPath, fullPath, null);
            }
            else
            {
                File.Move(tempPath, fullPath);
            }
        }

        private static string ToJson(GameStateSnapshot snapshot)
        {
            JsonBuilder json = new JsonBuilder();
            json.BeginObject();
            json.Property("source", snapshot.source);
            json.Property("updated_at_utc", snapshot.updated_at_utc);
            json.Property("hero", snapshot.hero);
            json.Property("day", snapshot.day);
            json.Property("event_options", snapshot.event_options);
            json.Property("event_option_ids", snapshot.event_option_ids);
            json.Property("event_option_template_ids", snapshot.event_option_template_ids);
            json.Property("owned_cards", snapshot.owned_cards);
            json.Property("visible_cards", snapshot.visible_cards);
            json.Property("gold", snapshot.gold);
            json.Property("health", snapshot.health);
            json.EndObject();
            json.NewLine();
            return json.ToString();
        }

        private sealed class JsonBuilder
        {
            private readonly StringBuilder builder = new StringBuilder();
            private readonly Stack<bool> firstStack = new Stack<bool>();

            public override string ToString()
            {
                return builder.ToString();
            }

            public void BeginObject()
            {
                BeforeValue();
                builder.Append('{');
                firstStack.Push(true);
            }

            public void EndObject()
            {
                builder.Append('}');
                firstStack.Pop();
            }

            public void NewLine()
            {
                builder.AppendLine();
            }

            public void Property(string name, string value)
            {
                WritePropertyName(name);
                WriteString(value);
            }

            public void Property(string name, int value)
            {
                WritePropertyName(name);
                builder.Append(value.ToString(CultureInfo.InvariantCulture));
            }

            public void Property(string name, int? value)
            {
                WritePropertyName(name);
                if (value.HasValue)
                {
                    builder.Append(value.Value.ToString(CultureInfo.InvariantCulture));
                }
                else
                {
                    builder.Append("null");
                }
            }

            public void Property(string name, List<string> values)
            {
                WritePropertyName(name);
                builder.Append('[');
                for (int i = 0; i < values.Count; i++)
                {
                    if (i > 0)
                    {
                        builder.Append(',');
                    }
                    WriteString(values[i]);
                }
                builder.Append(']');
            }

            public void Property(string name, List<CardSnapshot> cards)
            {
                WritePropertyName(name);
                builder.Append('[');
                for (int i = 0; i < cards.Count; i++)
                {
                    if (i > 0)
                    {
                        builder.Append(',');
                    }
                    WriteCard(cards[i]);
                }
                builder.Append(']');
            }

            private void WriteCard(CardSnapshot card)
            {
                builder.Append('{');
                bool wrote = false;
                WriteOptionalCardProperty("id", card.id, ref wrote);
                WriteOptionalCardProperty("template_id", card.template_id, ref wrote);
                WriteOptionalCardProperty("name", card.name, ref wrote);
                WriteOptionalCardProperty("rarity", card.rarity, ref wrote);
                WriteOptionalCardProperty("section", card.section, ref wrote);
                WriteOptionalCardProperty("card_type", card.card_type, ref wrote);
                WriteOptionalCardProperty("source", card.source, ref wrote);
                if (card.enchantments != null && card.enchantments.Count > 0)
                {
                    if (wrote)
                    {
                        builder.Append(',');
                    }
                    WriteString("enchantments");
                    builder.Append(':');
                    builder.Append('[');
                    for (int i = 0; i < card.enchantments.Count; i++)
                    {
                        if (i > 0)
                        {
                            builder.Append(',');
                        }
                        WriteString(card.enchantments[i]);
                    }
                    builder.Append(']');
                }
                builder.Append('}');
            }

            private void WriteOptionalCardProperty(string name, string value, ref bool wrote)
            {
                if (string.IsNullOrEmpty(value))
                {
                    return;
                }
                if (wrote)
                {
                    builder.Append(',');
                }
                WriteString(name);
                builder.Append(':');
                WriteString(value);
                wrote = true;
            }

            private void WritePropertyName(string name)
            {
                BeforeProperty();
                WriteString(name);
                builder.Append(':');
            }

            private void BeforeProperty()
            {
                if (firstStack.Count == 0)
                {
                    return;
                }

                bool first = firstStack.Pop();
                if (!first)
                {
                    builder.Append(',');
                }
                firstStack.Push(false);
            }

            private void BeforeValue()
            {
                if (firstStack.Count == 0)
                {
                    return;
                }
            }

            private void WriteString(string value)
            {
                if (value == null)
                {
                    builder.Append("null");
                    return;
                }

                builder.Append('"');
                for (int i = 0; i < value.Length; i++)
                {
                    char c = value[i];
                    switch (c)
                    {
                        case '"':
                            builder.Append("\\\"");
                            break;
                        case '\\':
                            builder.Append("\\\\");
                            break;
                        case '\b':
                            builder.Append("\\b");
                            break;
                        case '\f':
                            builder.Append("\\f");
                            break;
                        case '\n':
                            builder.Append("\\n");
                            break;
                        case '\r':
                            builder.Append("\\r");
                            break;
                        case '\t':
                            builder.Append("\\t");
                            break;
                        default:
                            if (c < 32)
                            {
                                builder.Append("\\u");
                                builder.Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                            }
                            else
                            {
                                builder.Append(c);
                            }
                            break;
                    }
                }
                builder.Append('"');
            }
        }
    }
}
