using System.Collections.Generic;

namespace BazaarStateExporter
{
    public sealed class GameStateSnapshot
    {
        public string source;
        public string status;
        public string message;
        public string updated_at_utc;
        public string state_signature;
        public string last_export_reason;
        public ExportDebugSnapshot debug;
        public string hero;
        public int day;
        public List<string> event_options = new List<string>();
        public List<string> event_option_ids = new List<string>();
        public List<string> event_option_template_ids = new List<string>();
        public List<EventOptionSnapshot> event_options_detailed = new List<EventOptionSnapshot>();
        public List<EventOptionSnapshot> current_events = new List<EventOptionSnapshot>();
        public List<CardSnapshot> owned_cards = new List<CardSnapshot>();
        public List<CardSnapshot> visible_cards = new List<CardSnapshot>();
        public List<CardSnapshot> owned_items = new List<CardSnapshot>();
        public List<CardSnapshot> board_items = new List<CardSnapshot>();
        public List<CardSnapshot> stash_items = new List<CardSnapshot>();
        public List<CardSnapshot> skills = new List<CardSnapshot>();
        public List<CardSnapshot> current_reward_options = new List<CardSnapshot>();
        public CurrentShopSnapshot current_shop;
        public int? gold;
        public int? health;
        public int? combat_health;
        public int? income;
        public int? level;
        public int? xp;
        public int? prestige;
        public int? max_prestige;
        public int? inventory_slots_used;
        public int? inventory_slots_total;

        public static GameStateSnapshot CreatePlaceholder()
        {
            return new GameStateSnapshot
            {
                source = "bepinex-placeholder",
                hero = "Vanessa",
                day = 6,
                event_options = new List<string> { "Colt", "Kina", "Gaseo" },
                owned_cards = new List<CardSnapshot>
                {
                    new CardSnapshot
                    {
                        name = "Ballista",
                        rarity = "gold",
                        enchantments = new List<string> { "Fiery" }
                    }
                },
                gold = 12,
                health = 43
            };
        }

        public static GameStateSnapshot CreateWaitingForGameState()
        {
            return new GameStateSnapshot
            {
                source = "bepinex",
                status = "waiting_for_game_state",
                message = "Bazaar State Exporter loaded, but no live run state has been captured yet. Start or restart the game and enter a run."
            };
        }
    }

    public sealed class ExportDebugSnapshot
    {
        public int export_count;
        public string screen_mode;
        public int event_option_count;
        public int visible_card_count;
        public int owned_card_count;
        public int shop_item_count;
        public int reward_option_count;
        public string dto_source;
        public string dto_summary;
    }

    public sealed class CurrentShopSnapshot
    {
        public string merchant_id;
        public string merchant_template_id;
        public string merchant_name;
        public List<CardSnapshot> visible_items = new List<CardSnapshot>();
        public bool? refresh_available;
        public int? refresh_cost;
        public int? refreshes_used;
        public int? refreshes_remaining;
    }

    public sealed class EventOptionSnapshot
    {
        public string id;
        public string template_id;
        public string name;
        public string kind;
        public string card_type;
        public string section;
        public string source;
        public List<EventOptionBranchSnapshot> branches = new List<EventOptionBranchSnapshot>();
    }

    public sealed class EventOptionBranchSnapshot
    {
        public string template_id;
        public string name;
        public string kind;
        public string card_type;
        public string source;
    }

    public sealed class CardSnapshot
    {
        public string id;
        public string template_id;
        public string name;
        public string rarity;
        public string section;
        public string card_type;
        public string source;
        public string ui_context;
        public int? price;
        public List<string> enchantments = new List<string>();
        public string runtime_type;
        public List<string> runtime_sources = new List<string>();
        public Dictionary<string, object> runtime_values = new Dictionary<string, object>();
        public Dictionary<string, object> current_attributes = new Dictionary<string, object>();
        public Dictionary<string, object> base_attributes = new Dictionary<string, object>();
        public Dictionary<string, object> attribute_modifiers = new Dictionary<string, object>();
    }
}
