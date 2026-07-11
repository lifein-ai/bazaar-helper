using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;

namespace BazaarStateExporter
{
    internal static class RuntimeCardInstanceReader
    {
        private const int MaxEntriesPerContainer = 96;

        private static readonly string[] CurrentAttributeMembers =
        {
            "Attributes",
            "CurrentAttributes",
            "ModifiedAttributes",
            "AttributeValues",
            "Stats",
            "CurrentStats",
            "RuntimeStats",
            "Values",
        };

        private static readonly string[] BaseAttributeMembers =
        {
            "BaseAttributes",
            "DefaultAttributes",
            "TemplateAttributes",
            "OriginalAttributes",
        };

        private static readonly string[] ModifierMembers =
        {
            "Modifiers",
            "AttributeModifiers",
            "StatModifiers",
            "Buffs",
            "Debuffs",
        };

        private static readonly string[] RuntimeValueMembers =
        {
            "Cooldown",
            "CooldownMax",
            "CurrentCooldown",
            "CooldownRemaining",
            "CooldownProgress",
            "Ammo",
            "MaxAmmo",
            "AmmoMax",
            "Charges",
            "Charge",
            "ChargeAmount",
            "Reload",
            "ReloadTime",
            "Haste",
            "Slow",
            "Freeze",
            "Frozen",
            "Disabled",
            "IsDisabled",
            "Active",
            "IsActive",
            "DamageAmount",
            "ShieldAmount",
            "BurnAmount",
            "PoisonAmount",
            "HealAmount",
            "RegenAmount",
            "CritChance",
            "CritMultiplier",
            "Value",
            "Amount",
            "Size",
            "Position",
            "Slot",
            "Index",
            "BoardIndex",
            "InventoryIndex",
        };

        public static void AddRuntimeSnapshot(CardSnapshot card, object source, string sourceName)
        {
            if (card == null || source == null)
            {
                return;
            }

            if (string.IsNullOrEmpty(card.runtime_type))
            {
                card.runtime_type = source.GetType().FullName;
            }

            if (!string.IsNullOrEmpty(sourceName)
                && !card.runtime_sources.Contains(sourceName))
            {
                card.runtime_sources.Add(sourceName);
            }

            ReadAttributeMembers(card.current_attributes, source, CurrentAttributeMembers);
            ReadAttributeMembers(card.base_attributes, source, BaseAttributeMembers);
            ReadAttributeMembers(card.attribute_modifiers, source, ModifierMembers);
            ReadRuntimeValues(card.runtime_values, source);
        }

        private static void ReadAttributeMembers(
            Dictionary<string, object> target,
            object source,
            IEnumerable<string> memberNames)
        {
            foreach (string memberName in memberNames)
            {
                object container = GetMemberValue(source, memberName);
                if (container == null)
                {
                    continue;
                }

                Dictionary<string, object> values = new Dictionary<string, object>(
                    StringComparer.OrdinalIgnoreCase);
                ReadContainer(values, container);
                foreach (KeyValuePair<string, object> item in values)
                {
                    if (target.Count >= MaxEntriesPerContainer)
                    {
                        return;
                    }
                    if (!target.ContainsKey(item.Key))
                    {
                        target[item.Key] = item.Value;
                    }
                }
            }
        }

        private static void ReadRuntimeValues(Dictionary<string, object> target, object source)
        {
            foreach (string memberName in RuntimeValueMembers)
            {
                object value = GetMemberValue(source, memberName);
                if (value == null)
                {
                    continue;
                }

                object simple = SimplifyValue(value);
                if (simple != null && !target.ContainsKey(memberName))
                {
                    target[memberName] = simple;
                }
            }

            foreach (MemberInfo member in GetReadableMembers(source.GetType()))
            {
                if (target.Count >= MaxEntriesPerContainer)
                {
                    return;
                }
                if (target.ContainsKey(member.Name) || !LooksLikeRuntimeValue(member.Name))
                {
                    continue;
                }

                object value = GetMemberValue(source, member);
                object simple = SimplifyValue(value);
                if (simple != null)
                {
                    target[member.Name] = simple;
                }
            }
        }

        private static bool LooksLikeRuntimeValue(string name)
        {
            if (string.IsNullOrEmpty(name))
            {
                return false;
            }

            string lower = name.ToLowerInvariant();
            return lower.Contains("cooldown")
                || lower.Contains("ammo")
                || lower.Contains("charge")
                || lower.Contains("reload")
                || lower.Contains("haste")
                || lower.Contains("slow")
                || lower.Contains("freeze")
                || lower.Contains("frozen")
                || lower.Contains("damage")
                || lower.Contains("shield")
                || lower.Contains("burn")
                || lower.Contains("poison")
                || lower.Contains("heal")
                || lower.Contains("regen")
                || lower.Contains("crit")
                || lower.Contains("slot")
                || lower.Contains("position")
                || lower.Contains("board")
                || lower.Contains("inventory")
                || lower.Contains("disabled");
        }

        private static void ReadContainer(Dictionary<string, object> target, object container)
        {
            if (container == null || container is string)
            {
                return;
            }

            IDictionary dictionary = container as IDictionary;
            if (dictionary != null)
            {
                foreach (DictionaryEntry entry in dictionary)
                {
                    AddContainerEntry(target, entry.Key, entry.Value);
                    if (target.Count >= MaxEntriesPerContainer)
                    {
                        return;
                    }
                }
                return;
            }

            IEnumerable enumerable = container as IEnumerable;
            if (enumerable != null)
            {
                int index = 0;
                foreach (object item in enumerable)
                {
                    object key = GetMemberValue(item, "Key")
                        ?? GetMemberValue(item, "Attribute")
                        ?? GetMemberValue(item, "Name")
                        ?? GetMemberValue(item, "Id")
                        ?? GetMemberValue(item, "Type");
                    object value = GetMemberValue(item, "Value")
                        ?? GetMemberValue(item, "Amount")
                        ?? GetMemberValue(item, "CurrentValue")
                        ?? GetMemberValue(item, "BaseValue")
                        ?? item;
                    AddContainerEntry(
                        target,
                        key ?? ("entry_" + index.ToString()),
                        value);
                    index++;
                    if (target.Count >= MaxEntriesPerContainer)
                    {
                        return;
                    }
                }
                return;
            }

            foreach (MemberInfo member in GetReadableMembers(container.GetType()))
            {
                if (target.Count >= MaxEntriesPerContainer)
                {
                    return;
                }

                object value = GetMemberValue(container, member);
                object simple = SimplifyValue(value);
                if (simple != null && !target.ContainsKey(member.Name))
                {
                    target[member.Name] = simple;
                }
            }
        }

        private static void AddContainerEntry(
            Dictionary<string, object> target,
            object key,
            object value)
        {
            string name = key == null ? null : key.ToString();
            object simple = SimplifyValue(value);
            if (string.IsNullOrEmpty(name) || simple == null || target.ContainsKey(name))
            {
                return;
            }

            target[name] = simple;
        }

        private static object SimplifyValue(object value)
        {
            if (value == null)
            {
                return null;
            }

            if (IsSimpleValue(value))
            {
                return NormalizeSimpleValue(value);
            }

            Type type = value.GetType();
            if (type.IsEnum)
            {
                return value.ToString();
            }

            object nested = GetMemberValue(value, "Value")
                ?? GetMemberValue(value, "Amount")
                ?? GetMemberValue(value, "CurrentValue")
                ?? GetMemberValue(value, "BaseValue")
                ?? GetMemberValue(value, "value")
                ?? GetMemberValue(value, "amount");
            if (nested != null && !ReferenceEquals(nested, value) && IsSimpleValue(nested))
            {
                return NormalizeSimpleValue(nested);
            }

            string text = value.ToString();
            return string.IsNullOrEmpty(text) || text == type.FullName
                ? null
                : text;
        }

        private static bool IsSimpleValue(object value)
        {
            if (value == null)
            {
                return false;
            }

            Type type = Nullable.GetUnderlyingType(value.GetType()) ?? value.GetType();
            return type.IsPrimitive
                || type.IsEnum
                || type == typeof(string)
                || type == typeof(decimal)
                || type == typeof(DateTime)
                || type == typeof(Guid)
                || type == typeof(TimeSpan);
        }

        private static object NormalizeSimpleValue(object value)
        {
            if (value == null)
            {
                return null;
            }

            Type type = Nullable.GetUnderlyingType(value.GetType()) ?? value.GetType();
            if (type.IsEnum)
            {
                return value.ToString();
            }
            return value;
        }

        private static object GetMemberValue(object target, string name)
        {
            if (target == null || string.IsNullOrEmpty(name))
            {
                return null;
            }

            Type type = target.GetType();
            FieldInfo field = type.GetField(
                name,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (field != null)
            {
                return SafeGet(() => field.GetValue(target));
            }

            PropertyInfo property = type.GetProperty(
                name,
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                return SafeGet(() => property.GetValue(target, null));
            }

            return null;
        }

        private static object GetMemberValue(object target, MemberInfo member)
        {
            if (target == null || member == null)
            {
                return null;
            }

            FieldInfo field = member as FieldInfo;
            if (field != null)
            {
                return SafeGet(() => field.GetValue(target));
            }

            PropertyInfo property = member as PropertyInfo;
            if (property != null && property.GetIndexParameters().Length == 0)
            {
                return SafeGet(() => property.GetValue(target, null));
            }

            return null;
        }

        private static IEnumerable<MemberInfo> GetReadableMembers(Type type)
        {
            BindingFlags flags =
                BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
            foreach (FieldInfo field in type.GetFields(flags))
            {
                yield return field;
            }
            foreach (PropertyInfo property in type.GetProperties(flags)
                .Where(property => property.GetIndexParameters().Length == 0))
            {
                yield return property;
            }
        }

        private static object SafeGet(Func<object> read)
        {
            try
            {
                return read();
            }
            catch
            {
                return null;
            }
        }
    }
}
