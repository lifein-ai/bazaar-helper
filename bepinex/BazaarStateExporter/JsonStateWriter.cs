using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;

namespace BazaarStateExporter
{
    public static class JsonStateWriter
    {
        private static readonly Encoding Utf8NoBom = new UTF8Encoding(false);
        private static readonly object WriteLock = new object();

        public static void WriteAtomic(string path, GameStateSnapshot snapshot)
        {
            lock (WriteLock)
            {
                string fullPath = Path.GetFullPath(Environment.ExpandEnvironmentVariables(path));
                string directory = Path.GetDirectoryName(fullPath);
                if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
                {
                    Directory.CreateDirectory(directory);
                }

                string tempPath = fullPath + "." + Guid.NewGuid().ToString("N") + ".tmp";
                try
                {
                    File.WriteAllText(tempPath, ToJson(snapshot), Utf8NoBom);
                    for (int attempt = 0; ; attempt++)
                    {
                        try
                        {
                            if (File.Exists(fullPath))
                            {
                                File.Replace(tempPath, fullPath, null);
                            }
                            else
                            {
                                File.Move(tempPath, fullPath);
                            }
                            return;
                        }
                        catch (Exception ex) when (ex is IOException || ex is UnauthorizedAccessException)
                        {
                            if (attempt >= 24)
                            {
                                throw;
                            }
                            Thread.Sleep(25 + attempt * 5);
                        }
                    }
                }
                finally
                {
                    try
                    {
                        if (File.Exists(tempPath))
                        {
                            File.Delete(tempPath);
                        }
                    }
                    catch (IOException)
                    {
                    }
                }
            }
        }

        private static string ToJson(GameStateSnapshot snapshot)
        {
            JsonBuilder json = new JsonBuilder();
            json.BeginObject();
            json.Property("source", snapshot.source);
            json.Property("status", snapshot.status);
            json.Property("message", snapshot.message);
            json.Property("updated_at_utc", snapshot.updated_at_utc);
            json.Property("state_signature", snapshot.state_signature);
            json.Property("last_export_reason", snapshot.last_export_reason);
            json.Property("debug", snapshot.debug);
            json.Property("hero", snapshot.hero);
            json.Property("day", snapshot.day);
            json.Property("event_options", snapshot.event_options);
            json.Property("event_option_ids", snapshot.event_option_ids);
            json.Property("event_option_template_ids", snapshot.event_option_template_ids);
            json.Property("event_options_detailed", snapshot.event_options_detailed);
            json.Property("current_events", snapshot.current_events);
            json.Property("owned_cards", snapshot.owned_cards);
            json.Property("visible_cards", snapshot.visible_cards);
            json.Property("owned_items", snapshot.owned_items);
            json.Property("board_items", snapshot.board_items);
            json.Property("stash_items", snapshot.stash_items);
            json.Property("skills", snapshot.skills);
            json.Property("current_reward_options", snapshot.current_reward_options);
            json.Property("current_shop", snapshot.current_shop);
            json.Property("gold", snapshot.gold);
            json.Property("health", snapshot.health);
            json.Property("combat_health", snapshot.combat_health);
            json.Property("income", snapshot.income);
            json.Property("level", snapshot.level);
            json.Property("xp", snapshot.xp);
            json.Property("prestige", snapshot.prestige);
            json.Property("max_prestige", snapshot.max_prestige);
            json.Property("inventory_slots_used", snapshot.inventory_slots_used);
            json.Property("inventory_slots_total", snapshot.inventory_slots_total);
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

            public void Property(string name, bool? value)
            {
                WritePropertyName(name);
                builder.Append(value.HasValue
                    ? (value.Value ? "true" : "false")
                    : "null");
            }

            public void Property(string name, CurrentShopSnapshot shop)
            {
                WritePropertyName(name);
                if (shop == null)
                {
                    builder.Append("null");
                    return;
                }

                builder.Append('{');
                WriteInlinePropertyName("visible_items", false);
                WriteCards(shop.visible_items);
                WriteInlinePropertyName("refresh_available", true);
                WriteNullableBool(shop.refresh_available);
                WriteInlinePropertyName("refresh_cost", true);
                WriteNullableInt(shop.refresh_cost);
                WriteInlinePropertyName("refreshes_used", true);
                WriteNullableInt(shop.refreshes_used);
                WriteInlinePropertyName("refreshes_remaining", true);
                WriteNullableInt(shop.refreshes_remaining);
                builder.Append('}');
            }

            public void Property(string name, ExportDebugSnapshot debug)
            {
                WritePropertyName(name);
                if (debug == null)
                {
                    builder.Append("null");
                    return;
                }

                builder.Append('{');
                WriteInlinePropertyName("export_count", false);
                builder.Append(debug.export_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("screen_mode", true);
                WriteString(debug.screen_mode);
                WriteInlinePropertyName("event_option_count", true);
                builder.Append(debug.event_option_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("visible_card_count", true);
                builder.Append(debug.visible_card_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("owned_card_count", true);
                builder.Append(debug.owned_card_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("shop_item_count", true);
                builder.Append(debug.shop_item_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("reward_option_count", true);
                builder.Append(debug.reward_option_count.ToString(CultureInfo.InvariantCulture));
                WriteInlinePropertyName("dto_source", true);
                WriteString(debug.dto_source);
                WriteInlinePropertyName("dto_summary", true);
                WriteString(debug.dto_summary);
                builder.Append('}');
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
                WriteCards(cards);
            }

            private void WriteCards(List<CardSnapshot> cards)
            {
                builder.Append('[');
                for (int i = 0; cards != null && i < cards.Count; i++)
                {
                    if (i > 0)
                    {
                        builder.Append(',');
                    }
                    WriteCard(cards[i]);
                }
                builder.Append(']');
            }

            private void WriteInlinePropertyName(string name, bool comma)
            {
                if (comma)
                {
                    builder.Append(',');
                }
                WriteString(name);
                builder.Append(':');
            }

            private void WriteNullableInt(int? value)
            {
                builder.Append(value.HasValue
                    ? value.Value.ToString(CultureInfo.InvariantCulture)
                    : "null");
            }

            private void WriteNullableBool(bool? value)
            {
                builder.Append(value.HasValue
                    ? (value.Value ? "true" : "false")
                    : "null");
            }
            public void Property(string name, List<EventOptionSnapshot> options)
            {
                WritePropertyName(name);
                builder.Append('[');

                if (options != null)
                {
                    for (int i = 0; i < options.Count; i++)
                    {
                        if (i > 0)
                        {
                            builder.Append(',');
                        }
                        WriteEventOption(options[i]);
                    }
                }

                builder.Append(']');
            }

            private void WriteEventOption(EventOptionSnapshot option)
            {
                if (option == null)
                {
                    builder.Append("{}");
                    return;
                }

                builder.Append('{');
                bool wrote = false;

                WriteOptionalCardProperty("id", option.id, ref wrote);
                WriteOptionalCardProperty("template_id", option.template_id, ref wrote);
                WriteOptionalCardProperty("name", option.name, ref wrote);
                WriteOptionalCardProperty("kind", option.kind, ref wrote);
                WriteOptionalCardProperty("card_type", option.card_type, ref wrote);
                WriteOptionalCardProperty("section", option.section, ref wrote);
                WriteOptionalCardProperty("source", option.source, ref wrote);

                builder.Append('}');
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
                WriteOptionalCardProperty("ui_context", card.ui_context, ref wrote);
                if (card.price.HasValue)
                {
                    if (wrote)
                    {
                        builder.Append(',');
                    }
                    WriteString("price");
                    builder.Append(':');
                    builder.Append(card.price.Value.ToString(CultureInfo.InvariantCulture));
                    wrote = true;
                }
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
