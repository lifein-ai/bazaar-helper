using System.Collections.Generic;

namespace BazaarStateExporter
{
    public sealed class GameStateSnapshot
    {
        public string source;
        public string updated_at_utc;
        public string hero;
        public int day;
        public List<string> event_options = new List<string>();
        public List<string> event_option_ids = new List<string>();
        public List<string> event_option_template_ids = new List<string>();
        public List<EventOptionSnapshot> event_options_detailed = new List<EventOptionSnapshot>();
        public List<CardSnapshot> owned_cards = new List<CardSnapshot>();
        public List<CardSnapshot> visible_cards = new List<CardSnapshot>();
        public int? gold;
        public int? health;

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
        public List<string> enchantments = new List<string>();
    }
}
