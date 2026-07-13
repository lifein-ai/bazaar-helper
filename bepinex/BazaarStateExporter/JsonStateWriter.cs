using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
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
                WriteInlinePropertyName("merchant_id", false);
                WriteString(shop.merchant_id);
                WriteInlinePropertyName("merchant_template_id", true);
                WriteString(shop.merchant_template_id);
                WriteInlinePropertyName("merchant_name", true);
                WriteString(shop.merchant_name);
                WriteInlinePropertyName("visible_items", true);
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
                WriteOptionalEventBranchesProperty("branches", option.branches, ref wrote);

                builder.Append('}');
            }

            private void WriteOptionalEventBranchesProperty(
                string name,
                List<EventOptionBranchSnapshot> branches,
                ref bool wrote)
            {
                if (branches == null || branches.Count == 0)
                {
                    return;
                }

                if (wrote)
                {
                    builder.Append(',');
                }
                WritePropertyNameOnly(name);
                builder.Append('[');
                for (int i = 0; i < branches.Count; i++)
                {
                    if (i > 0)
                    {
                        builder.Append(',');
                    }

                    EventOptionBranchSnapshot branch = branches[i];
                    if (branch == null)
                    {
                        builder.Append("{}");
                        continue;
                    }

                    builder.Append('{');
                    bool wroteBranch = false;
                    WriteOptionalCardProperty("template_id", branch.template_id, ref wroteBranch);
                    WriteOptionalCardProperty("name", branch.name, ref wroteBranch);
                    WriteOptionalCardProperty("kind", branch.kind, ref wroteBranch);
                    WriteOptionalCardProperty("card_type", branch.card_type, ref wroteBranch);
                    WriteOptionalCardProperty("source", branch.source, ref wroteBranch);
                    builder.Append('}');
                }
                builder.Append(']');
                wrote = true;
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
                WriteOptionalCardProperty("runtime_type", card.runtime_type, ref wrote);
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
                    wrote = true;
                }
                WriteOptionalStringListProperty(
                    "runtime_sources",
                    card.runtime_sources,
                    ref wrote);
                WriteOptionalObjectDictionaryProperty(
                    "runtime_values",
                    card.runtime_values,
                    ref wrote);
                WriteOptionalObjectDictionaryProperty(
                    "current_attributes",
                    card.current_attributes,
                    ref wrote);
                WriteOptionalObjectDictionaryProperty(
                    "base_attributes",
                    card.base_attributes,
                    ref wrote);
                WriteOptionalObjectDictionaryProperty(
                    "attribute_modifiers",
                    card.attribute_modifiers,
                    ref wrote);
                builder.Append('}');
            }

            private void WriteOptionalStringListProperty(
                string name,
                List<string> values,
                ref bool wrote)
            {
                if (values == null || values.Count == 0)
                {
                    return;
                }

                if (wrote)
                {
                    builder.Append(',');
                }
                WriteString(name);
                builder.Append(':');
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
                wrote = true;
            }

            private void WriteOptionalObjectDictionaryProperty(
                string name,
                Dictionary<string, object> values,
                ref bool wrote)
            {
                if (values == null || values.Count == 0)
                {
                    return;
                }

                if (wrote)
                {
                    builder.Append(',');
                }
                WriteString(name);
                builder.Append(':');
                WriteObjectDictionary(values);
                wrote = true;
            }

            private void WriteObjectDictionary(IDictionary values)
            {
                builder.Append('{');
                bool first = true;
                List<string> keys = new List<string>();
                foreach (object rawKey in values.Keys)
                {
                    if (rawKey != null)
                    {
                        keys.Add(rawKey.ToString());
                    }
                }
                keys.Sort(StringComparer.OrdinalIgnoreCase);

                foreach (string key in keys)
                {
                    if (!first)
                    {
                        builder.Append(',');
                    }
                    first = false;
                    WriteString(key);
                    builder.Append(':');
                    WriteAnyValue(values[key]);
                }
                builder.Append('}');
            }

            private void WriteAnyValue(object value)
            {
                if (value == null)
                {
                    builder.Append("null");
                    return;
                }

                if (value is string)
                {
                    WriteString((string)value);
                    return;
                }

                if (value is bool)
                {
                    builder.Append((bool)value ? "true" : "false");
                    return;
                }

                if (value is byte
                    || value is sbyte
                    || value is short
                    || value is ushort
                    || value is int
                    || value is uint
                    || value is long
                    || value is ulong
                    || value is float
                    || value is double
                    || value is decimal)
                {
                    builder.Append(Convert.ToString(value, CultureInfo.InvariantCulture));
                    return;
                }

                IDictionary dictionary = value as IDictionary;
                if (dictionary != null)
                {
                    WriteObjectDictionary(dictionary);
                    return;
                }

                IEnumerable enumerable = value as IEnumerable;
                if (enumerable != null)
                {
                    builder.Append('[');
                    bool first = true;
                    foreach (object item in enumerable)
                    {
                        if (!first)
                        {
                            builder.Append(',');
                        }
                        first = false;
                        WriteAnyValue(item);
                    }
                    builder.Append(']');
                    return;
                }

                WriteString(value.ToString());
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

            private void WritePropertyNameOnly(string name)
            {
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
