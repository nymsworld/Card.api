import os
import re
import sys
import csv
from datetime import datetime, timezone
import time
import json
import math
import requests

PAGE_SIZE = 250
REQUEST_DELAY = 1.5  # seconds between requests, to stay under the API's rate limit
MAX_RETRIES = 6
RETRY_STATUSES = (429, 500, 502, 503, 504)

API_BASE = "https://api.carddex.dev/compat/pokemontcg/v2"
CARD_FIELDS = "id,name,number,set,tcgplayer,images,rarity"

# Rarities pulled from the Pokemon TCG API (Scarlet & Violet + Mega Evolution only)
RARITY_QUERIES = ["Double Rare", "ACE SPEC Rare"]

# Individual cards stocked by request (a reverse holo entry is added when one exists)
SINGLE_CARD_IDS = [
    "sv7-57",       # Slowpoke 057/142 (Stellar Crown)
    "sv5-114",      # Metang 114/162 (Temporal Forces)
    "sv9-120",      # Dunsparce 120/159 (Journey Together)
    "sv5-161",      # Mist Energy 161/162 (Temporal Forces)
    "rsv10pt5-84",  # Hilda 084/086 (White Flare)
    "sv5-129",      # Dudunsparce 129/162 (Temporal Forces)
]

# Sets where every holofoil and reverse holo card is stocked
ALL_HOLO_SETS = ["sv3pt5"]  # Scarlet & Violet 151

# Sets where every card is stocked
ALL_CARD_SETS = ["me55c"]  # 30th Classic Collection

# Sets that get the condition picker on the price page (overnumbered cards always do)
CONDITION_PRICING_SETS = ["me55c"]  # 30th Classic Collection

# Every set in these eras is scanned for overnumbered (secret) cards
OVERNUMBERED_SERIES = ["Scarlet & Violet", "Mega Evolution"]
# Promos are numbered past their printed total by design, and Classic Collection
# reprints keep their original numbers (e.g. Magikarp 203 in a 30-card set)
OVERNUMBERED_SKIP_SETS = ["svp", "me55c"]

# Black Star Promo sets pulled straight from TCGplayer's data via tcgcsv.com
# (the Pokemon TCG API is missing many SV promos and has no Mega Evolution promos)
TCGCSV_BASE = "https://tcgcsv.com/tcgplayer/3"
# tcgcsv.com blocks requests that don't name the app sending them
TCGCSV_HEADERS = {"User-Agent": "KantoMisplaySociety-PriceSync/1.0"}
PROMO_GROUPS = [
    {"groupId": 22872, "prefix": "svp", "setName": "SV Black Star Promos"},
    {"groupId": 24451, "prefix": "mep", "setName": "ME Black Star Promos"},
]
PRICE_VARIANT_ORDER = ["Holofoil", "Normal", "Reverse Holofoil"]

# Sword & Shield era, straight from TCGplayer's data (tcgcsv.com): every Pokémon V / VMAX / VSTAR /
# V-UNION card and every overnumbered (secret) card in the era's sets, every card in the gallery
# subsets (Trainer Gallery, Galarian Gallery, Shiny Vault), and every card in the Sword & Shield
# Black Star Promos and Prize Pack Series. IDs are tcg-<TCGplayer product ID>, the same as stocked
# cards, so stickers already printed for stocked cards keep working. Entries carry "group", so
# part 5's rebuild of stocked cards leaves them alone (and doesn't list them twice).
SWSH_ERA_GROUP = re.compile(r"^(SWSH\d+:|SWSH: Crown Zenith|Pokemon GO$|Champion's Path$|Shining Fates|Celebrations$)")
SWSH_EXTRA_GROUPS = re.compile(r"^(SWSH: Sword & Shield Promo Cards|Prize Pack Series Cards)$")
SWSH_GALLERY = re.compile(r"(Trainer Gallery|Galarian Gallery|Shiny Vault)$")
V_FAMILY = re.compile(r"\b(V|VMAX|VSTAR|V-UNION)\b")

# Every Pokémon jumbo (oversized) card, from TCGplayer's "Jumbo Cards" group
JUMBO_GROUP = "Jumbo Cards"
# Scarlet & Violet / Mega Evolution commons, uncommons and rares worth more than CHEAP_MIN_PRICE,
# each printing on its own (a $2 reverse holo of a 20¢ common is added, its regular printing isn't).
# Once added a card stays and keeps its price updated, even if it dips below, so stickers printed
# for it keep working. Promo sets are covered above; Classic Collection reprints by ALL_CARD_SETS.
SV_ME_GROUP = re.compile(r"^(SV\d+|SV|ME\d+|ME):\s")
SV_ME_SKIP_GROUPS = re.compile(r"Promo|Classic Collection")
CHEAP_RARITIES = {"Common", "Uncommon", "Rare"}
CHEAP_MIN_PRICE = 0.50

# Variants that TCGplayer lists as their own products, from every English Pokémon group (part 6):
#   - stamped cards ("Reshiram ex (White Flare Stamped)", "[W Stamped]"...): all of them, any price.
#     Box and collection stamps are in TCGplayer's "Miscellaneous Cards & Products", not their set
#   - Battle Academy cards (deck-stamped) and ball / Energy Symbol pattern variants ("(Poke Ball
#     Pattern)", "(Dusk Ball)"...): those over CHEAP_MIN_PRICE, kept once added like the commons
# Printings already listed another way (e.g. the SV / ME commons step) are skipped.
STAMPED = re.compile(r"Stamp", re.IGNORECASE)
BALL_PATTERN = re.compile(r"\((Poke Ball|Poké Ball|Master Ball|Dusk Ball|Love Ball|Friend Ball|Quick Ball|Great Ball|Ultra Ball|Energy Symbol)( Pattern)?\)", re.IGNORECASE)
BATTLE_ACADEMY = re.compile(r"^Battle Academy")

# Sealed products (part 7): every booster box, ETB, blister, tin, collection, case... that TCGplayer lists in
# the Sword & Shield, Scarlet & Violet and Mega Evolution sets and the Prize Pack group (sealed = a product
# with no card number and no rarity). TCGplayer files era-wide tins and collections under
# "Miscellaneous Cards & Products", which has no era: those listed since Sword & Shield came out (product
# IDs from the first Sword & Shield card on) are added too. IDs are tcg-<product ID>, like stocked cards.
# No condition picker; with no TCGplayer market price the price is null, so the page shows "Ask staff"
# (a staff price from the app is used then, see "marketAt").
SEALED_GROUPS = re.compile(r"^(SWSH\d*:|SV\d*:|SVE:|ME\d*:|MEE:|Pokemon GO$|Champion's Path$|Shining Fates|Celebrations|Prize Pack Series|Trading Card Game Classic$)")
SEALED_MISC_GROUP = "Miscellaneous Cards & Products"
SEALED_ERA_START = "SWSH01: Sword & Shield Base Set"
SEALED_RARITY = "Sealed Product"

# Every English card by these illustrators, in every printing TCGplayer prices (regular, holo,
# reverse holo, 1st Edition, Unlimited...), from the Pokémon card API's artist field. Names must be
# spelled as printed on the cards; each run logs how many cards each name found.
ARTISTS = ["Mitsuhiro Arita", "Ken Sugimori", "Shinji Kanda", "Yuka Morii", "Kagemaru Himeno", "Tomokazu Komiya"]
# The card API's printing names: the first one a card has is its main entry (plain ID); reverse holos
# get -reverse like everywhere else; the rest get a suffix and a label, e.g. base1-4-1st-edition-holofoil
PRINTING_ORDER = ["holofoil", "normal", "unlimitedHolofoil", "unlimited", "1stEditionHolofoil", "1stEditionNormal", "1stEdition"]
PRINTING_LABELS = {
    "holofoil": "Holo", "normal": "Non-Holo", "unlimitedHolofoil": "Unlimited Holo", "unlimited": "Unlimited",
    "1stEditionHolofoil": "1st Edition Holo", "1stEditionNormal": "1st Edition", "1stEdition": "1st Edition",
}

# Cards the shop stocks, from a TCGplayer inventory export (only the product columns are
# kept: no quantities or prices). Every card listed gets condition pricing (NM-DMG), and
# cards not already in the database are added from TCGplayer's data. To update the list,
# replace this file with a new export trimmed to the same columns.
STOCK_FILE = "stock_cards.csv"
# Card games the stock list can include: TCGplayer product line -> TCGCSV category ID
TCGCSV_CATEGORIES = {"Pokemon": 3, "Pokemon Japan": 85, "Magic": 1, "YuGiOh": 2}
# Japanese Pokémon (TCGCSV category 85): every main Scarlet & Violet and Mega Evolution
# expansion (set codes like "SV7: ...", "SV2a: ...", "m1L: ...", "M6a: ..."), Double Rare and
# above, plus every card in the SV-P / M-P promo sets. IDs look like jp-<TCGplayer product ID>.
JAPAN_CATEGORY = 85
JAPAN_SET_CODE = re.compile(r"^(SV\d+[A-Za-z]?|[Mm]\d+[A-Za-z]?):\s")
JAPAN_PROMO_SETS = {"SV-P Promotional Cards", "M-P Promotional Cards"}
JAPAN_SKIP_RARITIES = {"", "Common", "Uncommon", "Rare", "None", "Code Card"}
# Other spellings of those product lines seen in TCGplayer exports
PRODUCT_LINE_ALIASES = {
    "Pokémon": "Pokemon",
    "Magic: The Gathering": "Magic",
    "MTG": "Magic",
    "Yu-Gi-Oh!": "YuGiOh",
    "Yu-Gi-Oh": "YuGiOh",
    "YuGiOh!": "YuGiOh",
}
# Shown on the price page and in the app
GAME_NAMES = {"Pokemon": "Pokémon", "Pokemon Japan": "Pokémon (Japanese)", "Magic": "Magic: The Gathering", "YuGiOh": "Yu-Gi-Oh!"}

# Bulk bin tags: fixed prices, no market lookup. Their QR stickers open the price page
# like any card, and the app's Sales tab can add them in quantity.
BULK_TAGS = [
    {"id": "bulk-100", "price": 1.00},
    {"id": "bulk-050", "price": 0.50},
    {"id": "bulk-025", "price": 0.25},
    {"id": "bulk-010", "price": 0.10},
]

# Rolling daily market prices, so the app can flag big price moves. Each run records
# today's price for every card; only the last week is kept (8 daily prices, so today
# can be compared with exactly 7 days ago).
HISTORY_FILE = "price_history.json"
VERSION_FILE = "prices_version.json"  # when the price files last changed: { updatedAt, part, cards, total, files }
# The card database is one file per card game: Pokémon (English and Japanese, and the bulk tags) stays in
# prices.json, other games go in their own file by the entry's "game" (empty, {}, until a game is stocked).
# The price page (GAME_FILES in index.html) and the shop app (GAME_FILES in price-cache.js) load and merge
# them, so a new file here needs adding there too. Shards and price history cover every game.
CATALOG_FILE = "prices.json"
GAME_FILES = {"Magic: The Gathering": "prices_magic.json", "Yu-Gi-Oh!": "prices_yugioh.json"}
# prices.json split into small files for the customer price page (see write_card_shards)
SHARD_DIR = "cards"
SHARD_COUNT = 128
HISTORY_DAYS = 8

# TCGplayer market prices by condition (LP / MP / HP / DMG) from JustTCG's free API, for
# condition-priced cards over CONDITION_MARKET_MIN (the price page's CONDITION_GUESS_MAX):
# below that the page uses a percent of Near Mint, and worn expensive cards sell for far less
# than a fixed percent. Written to condition_market.json (only this script writes it; staff
# prices from the app are in condition_prices.json and win over these). Free plan: 100
# requests/day, 1,000/month, 10/minute, 20 cards per request, so this runs once a week
# (part 5 or all; 6.5 days so the nightly run on day 7 always catches it) for at most
# CONDITION_MARKET_MAX_CARDS cards: about 11 requests a week. Needs the JUSTTCG_API_KEY repo
# secret; without it this step is skipped.
CONDITION_MARKET_FILE = "condition_market.json"
CONDITION_MARKET_MIN = 50
CONDITION_MARKET_MAX_CARDS = 400
CONDITION_MARKET_EVERY_HOURS = 6.5 * 24
JUSTTCG_URL = "https://api.justtcg.com/v1/cards"
JUSTTCG_BATCH = 20
JUSTTCG_DELAY = 6.5  # seconds between requests (10 per minute)
JUSTTCG_CONDITIONS = {"Lightly Played": "LP", "Moderately Played": "MP", "Heavily Played": "HP", "Damaged": "DMG"}
CONDITION_ORDER = ["LP", "MP", "HP", "DMG"]  # best to worst, after Near Mint
# Inconclusive condition prices are left out, so the price page shows "Ask staff" for them:
#   - stale: TCGplayer's last report for that condition is older than CONDITION_STALE_DAYS
#   - out of order: more than CONDITION_ORDER_SLACK above a better condition (or Near Mint).
#     Within the slack it's market noise, and the price is capped at the better condition's.
# The raw prices are kept in the file ("raw"), and these rules re-applied on every part 5 / all
# run, so changing a rule never needs a new JustTCG fetch.
CONDITION_STALE_DAYS = 30
CONDITION_ORDER_SLACK = 0.10

# The daily update runs in 4 parts, 30 minutes apart, so no single run sends
# enough requests to get rate limited. Each part only replaces its own cards.
PARTS = {
    "1": "Double Rares, ACE SPECs, single cards, 30th Classic Collection and promos",
    "2": "151 holos and reverse holos, overnumbered cards from the first half of Scarlet & Violet, and every card by the artists in ARTISTS",
    "3": "Overnumbered cards from the second half of Scarlet & Violet, jumbo cards, and SV / ME commons, uncommons and rares over $0.50",
    "4": "Overnumbered cards from Mega Evolution, then the Sword & Shield era: V cards, secrets, galleries, promos and Prize Packs",
    "5": "Japanese Pokémon cards, then stocked cards from stock_cards.csv (Pokémon, Magic, Yu-Gi-Oh) that aren't in the database yet",
    "6": "Stamped cards, Battle Academy cards and ball / Energy pattern variants from every TCGplayer Pokémon set",
    "7": "Sealed products from Sword & Shield, Scarlet & Violet, Mega Evolution and Prize Packs",
}

_tcgcsv_groups = None
_tcgcsv_prices = {}


def calculate_store_price(raw_market_price):
    if raw_market_price is None:
        return 1.00
    try:
        val = float(raw_market_price)
    except (ValueError, TypeError):
        return 1.00

    dollar_part = math.floor(val)
    cents_part = val - dollar_part
    rounded_price = (dollar_part + 1) if cents_part >= 0.30 else dollar_part
    return max(1.00, float(rounded_price))


def get_json(url, headers=None):
    """GETs a URL, waiting and retrying when the server says to slow down.
    Raises if it still fails, so a partial prices.json is never saved."""
    error = None
    for attempt in range(MAX_RETRIES):
        retry_after = None
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code == 200:
                return resp.json()
            error = f"HTTP {resp.status_code}"
            if resp.status_code not in RETRY_STATUSES:
                break
            retry_after = resp.headers.get("Retry-After")
        except requests.RequestException as e:
            error = str(e)

        wait = int(retry_after) if retry_after and retry_after.isdigit() else min(60, 5 * 2 ** attempt)
        print(f"  {error}, retrying in {wait}s...")
        time.sleep(wait)

    raise RuntimeError(f"Could not fetch {url}: {error}")


def fetch_cards(query):
    """Returns every card matching a Pokemon TCG API search, across all pages."""
    cards = []
    page = 1

    while True:
        url = f"{API_BASE}/cards?q={query}&page={page}&pageSize={PAGE_SIZE}&select={CARD_FIELDS}"
        data = get_json(url)
        page_cards = data.get("data", [])
        if not page_cards:
            break
        cards.extend(page_cards)

        if page * PAGE_SIZE >= data.get("totalCount", 0):
            break

        page += 1
        time.sleep(REQUEST_DELAY)

    time.sleep(REQUEST_DELAY)
    return cards


def market_price(card, variants):
    prices = card.get("tcgplayer", {}).get("prices", {})
    for variant in variants:
        if variant in prices and prices[variant].get("market") is not None:
            return prices[variant]["market"]
    return None


def has_variant(card, variant):
    return variant in card.get("tcgplayer", {}).get("prices", {})


def add_card(catalog, card, raw_market, card_id=None, reverse=False):
    card_id = card_id or card.get("id")
    name = card.get("name")
    rarity = card.get("rarity") or "Single"
    if reverse:
        name += " (Reverse Holo)"
        rarity += " Reverse Holo"

    entry = {
        "id": card_id,
        "name": name,
        "setName": card.get("set", {}).get("name", "Scarlet & Violet"),
        "number": card.get("number", "???"),
        "printedTotal": card.get("set", {}).get("printedTotal", ""),
        "rarity": rarity,
        "price": calculate_store_price(raw_market),
        "rawMarket": raw_market,
        "image": card.get("images", {}).get("small", ""),
        "tcgUrl": card.get("tcgplayer", {}).get("url", "https://www.tcgplayer.com")
    }
    # The price page shows a condition picker for these cards
    if is_overnumbered(card):
        entry["overnumbered"] = True
    if entry.get("overnumbered") or card.get("set", {}).get("id") in CONDITION_PRICING_SETS:
        entry["conditionPricing"] = True
    catalog[card_id] = entry


def add_reverse_holo(catalog, card):
    if has_variant(card, "reverseHolofoil"):
        add_card(catalog, card, market_price(card, ["reverseHolofoil"]),
                 card_id=f"{card['id']}-reverse", reverse=True)


def tcgplayer_reverse_price(card):
    """Looks up a card's reverse holo price straight from TCGplayer's data, for
    cards where the Pokemon TCG API is missing it. Returns None if there isn't one."""
    global _tcgcsv_groups
    match = re.search(r"/product/(\d+)", card.get("tcgplayer", {}).get("url", ""))
    if not match:
        return None

    if _tcgcsv_groups is None:
        groups = get_json(f"{TCGCSV_BASE}/groups", headers=TCGCSV_HEADERS).get("results", [])
        # "SV: White Flare" / "SV08: Surging Sparks" -> "white flare" / "surging sparks"
        _tcgcsv_groups = {g["name"].split(": ", 1)[-1].lower(): g["groupId"] for g in groups}

    group_id = _tcgcsv_groups.get(card.get("set", {}).get("name", "").lower())
    if not group_id:
        return None

    if group_id not in _tcgcsv_prices:
        rows = get_json(f"{TCGCSV_BASE}/{group_id}/prices", headers=TCGCSV_HEADERS).get("results", [])
        _tcgcsv_prices[group_id] = rows
        time.sleep(REQUEST_DELAY)

    for row in _tcgcsv_prices[group_id]:
        if row["productId"] == int(match.group(1)) and row.get("subTypeName") == "Reverse Holofoil":
            return row.get("marketPrice")
    return None


def is_overnumbered(card):
    if card.get("set", {}).get("id") in OVERNUMBERED_SKIP_SETS:
        return False
    number = str(card.get("number", ""))
    if number and not re.search(r"\d", number):
        return True  # letter-numbered secrets, e.g. the 30th Celebration RGB Mews
    match = re.match(r"\d+", number)
    printed_total = card.get("set", {}).get("printedTotal") or 0
    return bool(match) and printed_total > 0 and int(match.group()) > printed_total


def fetch_rarity(catalog, rarity):
    print(f"Fetching live prices for all {rarity} cards...")
    count = 0
    for card in fetch_cards(f'rarity:"{rarity}"'):
        series = card.get("set", {}).get("series", "")
        if "Scarlet" not in series and "Mega" not in series:
            continue
        add_card(catalog, card, market_price(card, ["holofoil", "normal", "reverseHolofoil"]))
        count += 1
    print(f"Synced {count} {rarity} cards")


def fetch_singles(catalog):
    print("Fetching live prices for individual cards...")
    query = " OR ".join(f"id:{card_id}" for card_id in SINGLE_CARD_IDS)
    cards = fetch_cards(query)
    for card in cards:
        add_card(catalog, card, market_price(card, ["holofoil", "normal", "reverseHolofoil"]))
        if has_variant(card, "reverseHolofoil"):
            add_reverse_holo(catalog, card)
        else:
            # e.g. Hilda 084/086: TCGplayer sells a reverse holo the API doesn't list
            reverse_price = tcgplayer_reverse_price(card)
            if reverse_price is not None:
                add_card(catalog, card, reverse_price, card_id=f"{card['id']}-reverse", reverse=True)

    found = set(card["id"] for card in cards)
    missing = [card_id for card_id in SINGLE_CARD_IDS if card_id not in found]
    print(f"Synced {len(cards)} individual cards" + (f" (not found: {', '.join(missing)})" if missing else ""))


def fetch_all_holos(catalog, set_id):
    print(f"Fetching live prices for every holo and reverse holo in {set_id}...")
    holos = reverses = 0
    for card in fetch_cards(f"set.id:{set_id}"):
        if has_variant(card, "holofoil"):
            add_card(catalog, card, market_price(card, ["holofoil"]))
            holos += 1
        if has_variant(card, "reverseHolofoil"):
            add_reverse_holo(catalog, card)
            reverses += 1
    print(f"Synced {holos} holofoils and {reverses} reverse holos from {set_id}")


def fetch_all_cards(catalog, set_id):
    print(f"Fetching live prices for every card in {set_id}...")
    cards = fetch_cards(f"set.id:{set_id}")
    for card in cards:
        add_card(catalog, card, market_price(card, ["holofoil", "normal", "reverseHolofoil"]))
    print(f"Synced {len(cards)} cards from {set_id}")


def overnumbered_sets(part):
    """Splits the overnumbered sets between parts 2, 3 and 4 (or returns all of them)."""
    sets = get_json(f"{API_BASE}/sets?pageSize=250").get("data", [])
    time.sleep(REQUEST_DELAY)
    sets = [s for s in sets if s.get("series") in OVERNUMBERED_SERIES and s.get("id") not in OVERNUMBERED_SKIP_SETS]
    sets.sort(key=lambda s: s.get("releaseDate", ""))

    sv_sets = [s for s in sets if s.get("series") == "Scarlet & Violet"]
    other_sets = [s for s in sets if s.get("series") != "Scarlet & Violet"]
    half = (len(sv_sets) + 1) // 2
    return {"2": sv_sets[:half], "3": sv_sets[half:], "4": other_sets}.get(part, sets)


def fetch_overnumbered(catalog, part):
    print("Fetching live prices for overnumbered cards...")
    for card_set in overnumbered_sets(part):
        count = 0
        for card in fetch_cards(f"set.id:{card_set['id']}"):
            if is_overnumbered(card):
                add_card(catalog, card, market_price(card, ["holofoil", "normal", "reverseHolofoil"]))
                count += 1
        print(f"  {card_set.get('name')}: {count} overnumbered cards")


def get_extended(product, field):
    for item in product.get("extendedData", []):
        if item.get("name") == field:
            return item.get("value")
    return None


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def variant_label(product_name):
    # "Pikachu - 027 (Pokemon Center Exclusive)" -> "Pokemon Center Exclusive"
    parts = re.findall(r"\(([^)]*)\)|\[([^\]]*)\]", product_name)
    return " ".join(a or b for a, b in parts)


def display_name(product_name):
    # "Meganium - 001 [Staff]" -> "Meganium [Staff]"
    return re.sub(r"\s+-\s+\d+[A-Za-z]?\b", "", product_name).strip()


def fetch_promo_group(catalog, group):
    print(f"Fetching live prices for {group['setName']}...")
    products = get_json(f"{TCGCSV_BASE}/{group['groupId']}/products", headers=TCGCSV_HEADERS).get("results", [])
    price_rows = get_json(f"{TCGCSV_BASE}/{group['groupId']}/prices", headers=TCGCSV_HEADERS).get("results", [])

    prices_by_product = {}
    for row in price_rows:
        prices_by_product.setdefault(row["productId"], {})[row.get("subTypeName")] = row.get("marketPrice")

    # Group products by card number so the plain version gets the simple ID
    # (svp-161) and stamped / staff / exclusive versions get a suffix
    by_number = {}
    for product in products:
        number = get_extended(product, "Number")
        if not number:
            continue
        # A few promos are listed as "134/142"; the promo number is the first part
        number = number.split("/")[0].strip()
        by_number.setdefault(number, []).append(product)

    added = 0
    for number, group_products in by_number.items():
        def plainness(p):
            name = p.get("name", "")
            return ("[" in name) * 2 + ("(" in name), p["productId"]

        group_products.sort(key=plainness)
        base_id = f"{group['prefix']}-{number.lstrip('0') or '0'}"

        for i, product in enumerate(group_products):
            card_id = base_id
            if i > 0:
                card_id = f"{base_id}-{slugify(variant_label(product['name'])) or product['productId']}"
                if card_id in catalog:
                    card_id = f"{base_id}-{product['productId']}"

            variants = prices_by_product.get(product["productId"], {})
            raw_market = None
            for variant in PRICE_VARIANT_ORDER:
                if variants.get(variant) is not None:
                    raw_market = variants[variant]
                    break
            if raw_market is None:
                raw_market = next((v for v in variants.values() if v is not None), None)

            catalog[card_id] = {
                "id": card_id,
                "name": display_name(product.get("name", "")),
                "setName": group["setName"],
                "number": number,
                "printedTotal": "",
                "rarity": "Promo",
                "price": calculate_store_price(raw_market),
                "rawMarket": raw_market,
                "image": product.get("imageUrl", ""),
                "tcgUrl": product.get("url", "https://www.tcgplayer.com")
            }
            added += 1

    print(f"Synced {added} cards from {group['setName']}")
    time.sleep(0.5)


def fetch_swsh_era(catalog):
    """Sword & Shield era cards (see SWSH_ERA_GROUP, SWSH_GALLERY, SWSH_EXTRA_GROUPS), as tcg-<product ID> entries."""
    groups = get_json(f"{TCGCSV_BASE}/groups", headers=TCGCSV_HEADERS).get("results", [])
    time.sleep(REQUEST_DELAY)
    wanted = [g for g in groups if SWSH_ERA_GROUP.match(g["name"]) or SWSH_EXTRA_GROUPS.match(g["name"])]
    print(f"Fetching live prices for Sword & Shield era cards from {len(wanted)} sets...")
    total = 0
    for g in wanted:
        # Galleries, promos and Prize Packs: every card; the main sets: V cards and secrets
        whole = bool(SWSH_GALLERY.search(g["name"]) or SWSH_EXTRA_GROUPS.match(g["name"]))
        products = get_json(f"{TCGCSV_BASE}/{g['groupId']}/products", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(REQUEST_DELAY)
        prices = {}
        for p in get_json(f"{TCGCSV_BASE}/{g['groupId']}/prices", headers=TCGCSV_HEADERS).get("results", []):
            prices.setdefault(p["productId"], {})[p.get("subTypeName")] = p.get("marketPrice")
        time.sleep(REQUEST_DELAY)
        # Drop only the set code: "SWSH07: Evolving Skies" -> "Evolving Skies", "SWSH: Crown Zenith: Galarian
        # Gallery" -> "Crown Zenith: Galarian Gallery", "Shining Fates: Shiny Vault" stays as it is
        set_name = re.sub(r"^SWSH\d*:\s*", "", g["name"])
        added = 0
        for product in products:
            data = {e.get("name"): e.get("value") for e in product.get("extendedData", [])}
            number = (data.get("Number") or "").strip()
            if not number:
                continue  # sealed products and other non-cards
            name = re.sub(r"\s+-\s+\S*\d\S*$", "", product["name"]).strip()
            card_number, _, printed_total = number.partition("/")
            card_number, printed_total = card_number.strip(), printed_total.strip()
            overnumbered = (card_number.isdigit() and printed_total.isdigit()
                            and int(card_number) > int(printed_total))
            if not (whole or overnumbered or V_FAMILY.search(name)):
                continue
            raw_market = pick_price(prices.get(product["productId"], {}), "Holofoil")
            card_id = f"tcg-{product['productId']}"
            entry = {
                "id": card_id,
                "name": name,
                "setName": set_name,
                "number": card_number,
                "printedTotal": printed_total,
                "rarity": (data.get("Rarity") or "").strip() or ("Promo" if "Promo" in g["name"] else "Single"),
                "price": calculate_store_price(raw_market),
                "rawMarket": raw_market,
                "image": product.get("imageUrl", ""),
                "tcgUrl": product.get("url") or f"https://www.tcgplayer.com/product/{product['productId']}",
                "game": GAME_NAMES["Pokemon"],
                "group": "swsh",
            }
            # Like the Scarlet & Violet secrets: overnumbered and gallery cards get the condition picker
            if overnumbered and "Prize Pack" not in g["name"] and "Promo" not in g["name"]:
                entry["overnumbered"] = True
            if entry.get("overnumbered") or SWSH_GALLERY.search(g["name"]):
                entry["conditionPricing"] = True
            catalog[card_id] = entry
            added += 1
        print(f"  {set_name}: {added} cards")
        total += added
    print(f"Synced {total} Sword & Shield era cards")


def tcgcsv_group(group_id):
    """(products, {productId: {printing: market price}}) for a TCGplayer Pokémon group."""
    products = get_json(f"{TCGCSV_BASE}/{group_id}/products", headers=TCGCSV_HEADERS).get("results", [])
    time.sleep(REQUEST_DELAY)
    prices = {}
    for p in get_json(f"{TCGCSV_BASE}/{group_id}/prices", headers=TCGCSV_HEADERS).get("results", []):
        prices.setdefault(p["productId"], {})[p.get("subTypeName")] = p.get("marketPrice")
    time.sleep(REQUEST_DELAY)
    return products, prices


def tcgcsv_entry(product, card_id, set_name, raw_market, group, reverse=False):
    """A prices.json entry for a TCGplayer product (tcg- IDs, like stocked cards)."""
    data = {e.get("name"): e.get("value") for e in product.get("extendedData", [])}
    number, _, printed_total = (data.get("Number") or "").partition("/")
    # "Charizard EX - XY17 (XY Black Star Promos)" -> "Charizard EX (XY Black Star Promos)"
    name = re.sub(r"\s+-\s+\S*\d\S*", "", product["name"]).strip()
    return {
        "id": card_id,
        "name": name + (" (Reverse Holo)" if reverse else ""),
        "setName": set_name,
        "number": number.strip(),
        "printedTotal": printed_total.strip(),
        "rarity": ((data.get("Rarity") or "Single").strip() + (" Reverse Holo" if reverse else "")),
        "price": calculate_store_price(raw_market),
        "rawMarket": raw_market,
        "image": product.get("imageUrl", ""),
        "tcgUrl": product.get("url") or f"https://www.tcgplayer.com/product/{product['productId']}",
        "game": GAME_NAMES["Pokemon"],
        "group": group,
    }


def fetch_jumbo(catalog, groups):
    """Every card in TCGplayer's Jumbo Cards group."""
    group = next((g for g in groups if g["name"] == JUMBO_GROUP), None)
    if not group:
        print("  No Jumbo Cards group on TCGplayer: skipped")
        return
    products, prices = tcgcsv_group(group["groupId"])
    for product in products:
        raw_market = pick_price(prices.get(product["productId"], {}), "Holofoil")
        card_id = f"tcg-{product['productId']}"
        catalog[card_id] = tcgcsv_entry(product, card_id, "Jumbo Cards", raw_market, "jumbo")
    print(f"Synced {len(products)} jumbo cards")


def fetch_sv_me_commons(catalog, groups, existing):
    """Scarlet & Violet / Mega Evolution commons, uncommons and rares over CHEAP_MIN_PRICE (see above).
    existing: prices.json as it is now, to keep cards added before and skip printings already listed."""
    covered = {}
    for card_id, entry in existing.items():
        key = entry_product_key(card_id, entry)
        if key and not is_stock_entry(card_id, entry):
            covered[key] = entry.get("group")
    wanted = [g for g in groups if SV_ME_GROUP.match(g["name"]) and not SV_ME_SKIP_GROUPS.search(g["name"])]
    print(f"Fetching commons, uncommons and rares over ${CHEAP_MIN_PRICE:.2f} from {len(wanted)} Scarlet & Violet / Mega Evolution sets...")
    added = 0
    for g in wanted:
        products, prices = tcgcsv_group(g["groupId"])
        set_name = re.sub(r"^(SV\d*|ME\d*):\s*", "", g["name"])
        for product in products:
            data = {e.get("name"): e.get("value") for e in product.get("extendedData", [])}
            if (data.get("Rarity") or "").strip() not in CHEAP_RARITIES:
                continue
            variants = prices.get(product["productId"], {})
            pid = str(product["productId"])
            printings = [("base", f"tcg-{pid}", variants.get("Holofoil") if variants.get("Holofoil") is not None else variants.get("Normal")),
                         ("reverse", f"tcg-{pid}-reverse", variants.get("Reverse Holofoil"))]
            for kind, card_id, raw_market in printings:
                if raw_market is None:
                    continue
                owner = covered.get((pid, kind), "none")
                if owner not in ("none", "svme-cheap"):
                    continue  # already listed another way (e.g. the 151 holos, single cards)
                if raw_market <= CHEAP_MIN_PRICE and owner != "svme-cheap":
                    continue
                catalog[card_id] = tcgcsv_entry(product, card_id, set_name, raw_market, "svme-cheap", reverse=kind == "reverse")
                added += 1
    print(f"Synced {added} commons, uncommons and rares")


def fetch_variants(catalog, groups, existing):
    """Stamped cards, Battle Academy cards and ball / Energy pattern variants (see STAMPED above), as
    tcg- entries. existing: prices.json as it is now (kept cards, and printings listed another way)."""
    covered = {}
    for card_id, entry in existing.items():
        key = entry_product_key(card_id, entry)
        if key and not is_stock_entry(card_id, entry):
            covered[key] = entry.get("group")
    print(f"Looking for stamped cards and pattern variants in {len(groups)} sets...")
    counts = {"stamped": 0, "pattern": 0, "battle academy": 0}
    for g in groups:
        products = get_json(f"{TCGCSV_BASE}/{g['groupId']}/products", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(0.5)
        candidates = []
        for product in products:
            data = {e.get("name"): e.get("value") for e in product.get("extendedData", [])}
            if not data.get("Number"):
                continue  # sealed products, boxes, code cards
            name = product.get("name", "")
            if BATTLE_ACADEMY.match(g["name"]):
                kind = "battle academy"
            elif STAMPED.search(name) and not re.search(r"^(Unfair|Reset) Stamp\b", name):  # trainer cards named "... Stamp"
                kind = "stamped"
            elif BALL_PATTERN.search(name):
                kind = "pattern"
            else:
                continue
            owner = covered.get((str(product["productId"]), "base"), "none")
            if owner not in ("none", "variants"):
                continue  # already listed another way
            candidates.append((product, kind, owner))
        if not candidates:
            continue
        prices = {}
        for p in get_json(f"{TCGCSV_BASE}/{g['groupId']}/prices", headers=TCGCSV_HEADERS).get("results", []):
            prices.setdefault(p["productId"], {})[p.get("subTypeName")] = p.get("marketPrice")
        time.sleep(0.5)
        set_name = re.sub(r"^(SV\d*|ME\d*|SWSH\d*|SM\d*|XY\d*):\s*", "", g["name"])
        for product, kind, owner in candidates:
            raw_market = pick_price(prices.get(product["productId"], {}), "Holofoil")
            if kind != "stamped" and owner != "variants" and (raw_market or 0) <= CHEAP_MIN_PRICE:
                continue
            card_id = f"tcg-{product['productId']}"
            catalog[card_id] = tcgcsv_entry(product, card_id, set_name, raw_market, "variants")
            counts[kind] += 1
    print(f"Synced {counts['stamped']} stamped cards, {counts['pattern']} pattern variants and {counts['battle academy']} Battle Academy cards")


def is_sealed(product):
    data = {e.get("name") for e in product.get("extendedData", [])}
    return not data & {"Number", "Rarity"}


def fetch_sealed(catalog, groups, previous):
    """Sealed products (see SEALED_GROUPS). previous: prices.json as it is now, for each product's "marketAt":
    when TCGplayer's market price last changed. A staff price set after that wins over TCGplayer's (the
    owner's rule for condition prices: a staff price holds until TCGplayer has newer data)."""
    now = datetime.now(timezone.utc).isoformat()
    wanted = [g for g in groups if SEALED_GROUPS.match(g["name"])]
    misc = next((g for g in groups if g["name"] == SEALED_MISC_GROUP), None)
    print(f"Fetching sealed products from {len(wanted)} sets" + (" and Miscellaneous Cards & Products" if misc else "") + "...")
    first_id = None  # the first Sword & Shield product ID, for the Miscellaneous group (which comes last)
    added = priced = 0
    for g in wanted + ([misc] if misc else []):
        products, prices = tcgcsv_group(g["groupId"])
        if g["name"] == SEALED_ERA_START:
            first_id = min((p["productId"] for p in products), default=None)
        if g is misc:
            if first_id is None:
                print("  No Sword & Shield start found: Miscellaneous Cards & Products skipped")
                continue
            products = [p for p in products if p["productId"] >= first_id]
        set_name = re.sub(r"^(SWSH\d*|SV\d*|SVE|ME\d*|MEE):\s*", "", g["name"])
        for product in products:
            if not is_sealed(product):
                continue
            raw_market = pick_price(prices.get(product["productId"], {}), "Normal")
            card_id = f"tcg-{product['productId']}"
            before = previous.get(card_id) or {}
            same = before.get("group") == "sealed" and before.get("rawMarket") == raw_market
            catalog[card_id] = {
                "id": card_id,
                "name": re.sub(r"\s+", " ", product["name"]).strip(),
                "setName": set_name,
                "number": "",
                "printedTotal": "",
                "rarity": SEALED_RARITY,
                "price": calculate_store_price(raw_market) if raw_market is not None else None,
                "rawMarket": raw_market,
                "marketAt": (before.get("marketAt") if same else None) or now,
                "image": product.get("imageUrl", ""),
                "tcgUrl": product.get("url") or f"https://www.tcgplayer.com/product/{product['productId']}",
                "game": GAME_NAMES["Pokemon"],
                "group": "sealed",
                "sealed": True,
            }
            added += 1
            priced += raw_market is not None
    print(f"Synced {added} sealed products ({added - priced} without a TCGplayer market price: Ask staff)")


def fetch_artists(catalog, existing):
    """Every card by the illustrators in ARTISTS, one entry per priced printing.
    existing: prices.json as it is now; printings already there as stocked cards are left to them,
    so stickers printed with their tcg- IDs keep working."""
    stocked = {entry_product_key(k, v) for k, v in existing.items() if is_stock_entry(k, v)} - {None}
    print("Fetching live prices for cards by " + ", ".join(ARTISTS) + "...")
    for artist in ARTISTS:
        cards = fetch_cards(f'artist:"{artist}"')
        if not cards:
            print(f"  WARNING: no cards found for {artist} (check the spelling on the cards)")
            continue
        entries = 0
        for card in cards:
            prices = card.get("tcgplayer", {}).get("prices", {})
            main = next((p for p in PRINTING_ORDER if p in prices), None)
            match = re.search(r"/product/(\d+)", card.get("tcgplayer", {}).get("url", ""))
            pid = match.group(1) if match else None
            for printing in [p for p in PRINTING_ORDER if p in prices] + (["reverseHolofoil"] if "reverseHolofoil" in prices else []) or [None]:
                reverse = printing == "reverseHolofoil"
                if pid and (pid, printing_kind(printing)) in stocked:
                    continue  # a stocked card with its own sticker ID
                raw_market = market_price(card, [printing]) if printing else None
                if printing and raw_market is None and printing != main:
                    continue  # printings with no price yet; the main one is listed anyway
                if reverse:
                    card_id = f"{card['id']}-reverse"
                elif printing in (main, None):
                    card_id = card["id"]
                else:
                    card_id = f"{card['id']}-{slugify(PRINTING_LABELS[printing])}"
                add_card(catalog, card, raw_market, card_id=card_id, reverse=reverse)
                if printing and not reverse and printing != main:
                    catalog[card_id]["name"] += f" ({PRINTING_LABELS[printing]})"
                if printing_kind(printing) == "1st" and "-1st-edition" not in card_id:
                    catalog[card_id]["firstEdition"] = True  # a card only priced as 1st Edition keeps its plain ID
                catalog[card_id]["artist"] = card.get("artist") or artist
                entries += 1
        print(f"  {artist}: {len(cards)} cards, {entries} printings")


def is_stock_entry(card_id, entry):
    """A card that's in prices.json only because it's on the stock list (rebuilt by part 5)."""
    return card_id.startswith("tcg-") and not entry.get("group")


def load_stock():
    try:
        with open(STOCK_FILE, encoding="utf-8-sig", newline="") as f:
            return [row for row in csv.DictReader(f) if row.get("Product ID")]
    except OSError:
        return []


def fetch_japanese(catalog):
    """Japanese Scarlet & Violet / Mega Evolution cards, Double Rare and up, plus SV-P / M-P promos."""
    base = f"https://tcgcsv.com/tcgplayer/{JAPAN_CATEGORY}"
    groups = get_json(f"{base}/groups", headers=TCGCSV_HEADERS).get("results", [])
    time.sleep(REQUEST_DELAY)
    wanted = [g for g in groups if JAPAN_SET_CODE.match(g["name"]) or g["name"] in JAPAN_PROMO_SETS]
    print(f"Fetching live prices for Japanese cards from {len(wanted)} sets...")
    added = 0
    for g in wanted:
        promo = g["name"] in JAPAN_PROMO_SETS
        products = get_json(f"{base}/{g['groupId']}/products", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(REQUEST_DELAY)
        prices = {}
        for p in get_json(f"{base}/{g['groupId']}/prices", headers=TCGCSV_HEADERS).get("results", []):
            prices.setdefault(p["productId"], {})[p.get("subTypeName")] = p.get("marketPrice")
        time.sleep(REQUEST_DELAY)
        set_name = g["name"].split(": ", 1)[-1] if not promo else g["name"].replace(" Promotional Cards", " Promos")
        for product in products:
            data = {e.get("name"): e.get("value") for e in product.get("extendedData", [])}
            rarity = (data.get("Rarity") or "").strip()
            number = (data.get("Number") or "").strip()
            if not number or (not promo and rarity in JAPAN_SKIP_RARITIES):
                continue  # sealed products, code cards and lower rarities
            raw_market = pick_price(prices.get(product["productId"], {}), "Holofoil")
            card_number, _, total = number.partition("/")
            card_id = f"jp-{product['productId']}"
            catalog[card_id] = {
                "id": card_id,
                "name": re.sub(r"\s+-\s+\S*\d\S*$", "", product["name"]).strip(),
                "setName": set_name,
                "number": card_number.strip(),
                "printedTotal": total.strip(),
                "rarity": rarity or "Promo",
                "price": calculate_store_price(raw_market),
                "rawMarket": raw_market,
                "image": product.get("imageUrl", ""),
                "tcgUrl": product.get("url") or f"https://www.tcgplayer.com/product/{product['productId']}",
                "conditionPricing": True,
                "game": GAME_NAMES["Pokemon Japan"],
            }
            added += 1
    print(f"Synced {added} Japanese cards")


def product_line(row):
    """The row's card game as a TCGCSV_CATEGORIES key, e.g. 'Magic: The Gathering' -> 'Magic'."""
    line = (row.get("Product Line") or "Pokemon").strip()
    return PRODUCT_LINE_ALIASES.get(line, line)


def printing_kind(printing):
    """'reverse', '1st' or 'base' for a printing name, TCGplayer's ("1st Edition Holofoil") or the card
    API's ("1stEditionHolofoil"). 1st Edition and Unlimited share one TCGplayer product but are priced
    (and sold) separately, so they're different entries; other printings count as the base card."""
    p = (printing or "").lower()
    if "reverse" in p:
        return "reverse"
    return "1st" if "1st" in p else "base"


def entry_product_key(card_id, entry):
    """(TCGplayer product ID, 'base' / 'reverse' / '1st') for a prices.json entry, from its ID's suffix
    (-reverse; -1st-edition... for 1st Edition entries added from the stock list or the artists' cards), or
    firstEdition on an artist's card whose only priced printing is 1st Edition (it keeps its plain ID)."""
    match = re.search(r"/product/(\d+)", entry.get("tcgUrl", ""))
    if not match:
        return None
    if card_id.endswith("-reverse"):
        return match.group(1), "reverse"
    return match.group(1), "1st" if "-1st-edition" in card_id or entry.get("firstEdition") else "base"


def pick_price(variants, printing):
    """Market price for the stocked printing, e.g. 'Holofoil' -> 'Unlimited Holofoil' for vintage cards."""
    if variants.get(printing) is not None:
        return variants[printing]
    wanted = (printing or "").lower()
    close = [name for name in variants if wanted and wanted in name.lower() and variants[name] is not None]
    if close:
        # Prefer the regular printing over 1st Edition unless 1st Edition was asked for
        close.sort(key=lambda name: ("1st" in name.lower()) != ("1st" in wanted))
        return variants[close[0]]
    for name in ["Holofoil", "Normal", "Unlimited Holofoil", "Unlimited", "Reverse Holofoil", "Foil", "1st Edition", "Limited"]:
        if variants.get(name) is not None:
            return variants[name]
    return next((v for v in variants.values() if v is not None), None)


def fetch_stock(catalog, stock, existing):
    """Adds stocked cards that no existing entry covers, priced from TCGplayer's data."""
    covered = set()
    for card_id, entry in existing.items():
        key = entry_product_key(card_id, entry)
        if key:
            covered.add(key)
    todo = [row for row in stock if (row["Product ID"], printing_kind(row["Printing"])) not in covered]
    print(f"Fetching live prices for {len(todo)} stocked cards not already in the database...")
    if not todo:
        return

    # Only look up the games that are actually stocked
    groups = {}
    needed = {product_line(row) for row in todo}
    for line, category in TCGCSV_CATEGORIES.items():
        if line not in needed:
            continue
        for g in get_json(f"https://tcgcsv.com/tcgplayer/{category}/groups", headers=TCGCSV_HEADERS).get("results", []):
            groups[(line, g["name"])] = (category, g["groupId"], g["name"])
        time.sleep(REQUEST_DELAY)
    for line in needed - set(TCGCSV_CATEGORIES):
        print(f"  Skipping {line} cards: not a supported card game")

    def find_group(line, set_name):
        if (line, set_name) in groups:
            return groups[(line, set_name)]
        # e.g. "Power Keepers" is listed as "EX Power Keepers"
        matches = [g for (l, name), g in groups.items() if l == line and name.endswith(" " + set_name)]
        return matches[0] if len(matches) == 1 else None

    by_group = {}
    for row in todo:
        line = product_line(row)
        if line not in TCGCSV_CATEGORIES:
            continue
        group = find_group(line, row["Set Name"])
        if not group:
            print(f"  Skipped {row['Product Name']}: no TCGplayer {line} set named {row['Set Name']}")
            continue
        by_group.setdefault((group, line), []).append(row)

    added = 0
    for ((category, group_id, group_name), line), rows in by_group.items():
        prices = {}
        for p in get_json(f"https://tcgcsv.com/tcgplayer/{category}/{group_id}/prices", headers=TCGCSV_HEADERS).get("results", []):
            prices.setdefault(str(p["productId"]), {})[p.get("subTypeName")] = p.get("marketPrice")
        time.sleep(REQUEST_DELAY)

        for row in rows:
            pid = row["Product ID"]
            printing = row["Printing"] or ""
            kind = printing_kind(printing)
            if kind == "reverse":
                suffix, label = "-reverse", " (Reverse Holo)"
            elif printing in ("Holofoil", "Normal", ""):
                suffix, label = "", ""
            else:
                suffix = "-" + slugify(printing)
                if "1st" in printing:
                    label = " (1st Edition)"
                elif "foil" in printing.lower():
                    label = " (Foil)"
                else:
                    label = ""
            number, _, total = (row.get("Number") or "").partition("/")
            raw_market = pick_price(prices.get(pid, {}), printing)
            card_id = f"tcg-{pid}{suffix}"
            catalog[card_id] = {
                "id": card_id,
                "name": re.sub(r"\s+-\s+\S*\d\S*$", "", row["Product Name"]).strip() + label,
                # Pokémon set names start with a set code ("SV07: Stellar Crown"); other games keep the full name
                "setName": group_name.split(": ", 1)[-1] if line.startswith("Pokemon") else group_name,
                "number": number.strip() or "???",
                "printedTotal": total.strip(),
                "rarity": row.get("Rarity") or "Single",
                "price": calculate_store_price(raw_market),
                "rawMarket": raw_market,
                "image": row.get("Photo URL") or "",
                "tcgUrl": f"https://www.tcgplayer.com/product/{pid}",
                "conditionPricing": True,
                "game": GAME_NAMES.get(line, line),
            }
            added += 1
    print(f"Synced {added} stocked cards from {len(by_group)} sets")


def mark_stocked(catalog, stock):
    """Every stocked card gets the condition picker, whichever part added it. Matched loosely (reverse holo or
    not): a stocked 1st Edition also turns it on for the Unlimited printing and the other way round, as it
    always has, so no card the shop sells loses its condition prices."""
    def loose(key):
        return key and (key[0], "reverse" if key[1] == "reverse" else "base")
    stocked = {loose((row["Product ID"], printing_kind(row["Printing"]))) for row in stock}
    marked = 0
    for card_id, entry in catalog.items():
        if entry.get("sealed"):
            continue  # sealed products have no conditions
        if loose(entry_product_key(card_id, entry)) in stocked and not entry.get("conditionPricing"):
            entry["conditionPricing"] = True
            marked += 1
    if marked:
        print(f"Turned on condition pricing for {marked} stocked cards")


def add_bulk_tags(catalog):
    for tag in BULK_TAGS:
        label = f"${tag['price']:.2f}"
        catalog[tag["id"]] = {
            "id": tag["id"],
            "name": f"{label} Bulk Card",
            "setName": "Bulk Bin",
            "number": "",
            "printedTotal": "",
            "rarity": "Bulk",
            "price": tag["price"],  # fixed price: no $1 store minimum
            "rawMarket": None,
            "image": "",
            "tcgUrl": "",
            "bulk": True,
        }


def update_history(catalog):
    """Records today's market price for every card in price_history.json:
    { "dates": [...], "prices": { cardId: [price per date, null if unknown] } }"""
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    except (OSError, ValueError):
        history = {"dates": [], "prices": {}}

    dates = history.get("dates", [])
    prices = history.get("prices", {})
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if not dates or dates[-1] != today:
        dates.append(today)
    count = len(dates)

    for card_id, entry in catalog.items():
        series = prices.get(card_id, [])
        series = series + [None] * (count - len(series))
        if entry.get("rawMarket") is not None:
            series[-1] = round(float(entry["rawMarket"]), 2)
        prices[card_id] = series

    # Keep the last HISTORY_DAYS days, and only cards that still exist
    keep = max(0, count - HISTORY_DAYS)
    history = {
        "dates": dates[keep:],
        "prices": {k: v[keep:] for k, v in prices.items() if k in catalog and any(p is not None for p in v[keep:])},
    }
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, separators=(",", ":"))
    print(f"Price history: {len(history['dates'])} day(s) for {len(history['prices'])} cards")


def pick_condition_prices(card, entry, card_id):
    """{ 'LP': 41.5, ... } from a JustTCG card for a prices.json entry: the printing that
    matches (reverse holo or not) with the Near Mint price closest to ours, in the card's
    language (Japanese for jp- cards, else English; JustTCG labels English listings "English")."""
    want_reverse = card_id.endswith("-reverse")
    want_language = "japanese" if card_id.startswith("jp-") else "english"
    printings = {}
    for v in card.get("variants", []):
        if (v.get("language") or "english").lower() != want_language:
            continue
        if ("reverse" in (v.get("printing") or "").lower()) != want_reverse:
            continue
        printings.setdefault(v.get("printing"), []).append(v)
    best, best_gap = None, None
    for variants in printings.values():
        nm = next((v.get("price") for v in variants if v.get("condition") == "Near Mint" and v.get("price") is not None), None)
        gap = abs(nm - entry["rawMarket"]) if nm is not None else float("inf")
        if best is None or gap < best_gap:
            best, best_gap = variants, gap
    prices = {}
    for v in best or []:
        code = JUSTTCG_CONDITIONS.get(v.get("condition"))
        if code and v.get("price") is not None:
            prices[code] = {"price": round(float(v["price"]), 2), "updated": v.get("lastUpdated")}
    return prices


def clean_condition_prices(raw, near_mint, counts):
    """Usable prices ({ 'LP': 41.5, ... }) from one card's raw prices ({ 'LP': {price, updated} }),
    leaving out inconclusive ones (see CONDITION_STALE_DAYS / CONDITION_ORDER_SLACK). counts
    collects how many were 'capped', 'stale' and 'outOfOrder'."""
    now = time.time()
    clean = {}
    better = near_mint
    for code in CONDITION_ORDER:
        item = raw.get(code)
        if not item or item.get("price") is None:
            continue
        price = item["price"]
        updated = item.get("updated")
        if updated and now - updated > CONDITION_STALE_DAYS * 86400:
            counts["stale"] += 1
            continue
        if better is not None and price > better:
            if price > better * (1 + CONDITION_ORDER_SLACK):
                counts["outOfOrder"] += 1
                continue
            counts["capped"] += 1
            price = better
        clean[code] = round(price, 2)
        better = price
    return clean


def write_condition_prices(data, catalog):
    """Re-applies the inconclusive-price rules to data["raw"] and saves the file."""
    counts = {"capped": 0, "stale": 0, "outOfOrder": 0}
    prices = {}
    for card_id, raw in data.get("raw", {}).items():
        entry = catalog.get(card_id)
        if not entry:
            continue
        clean = clean_condition_prices(raw, entry.get("rawMarket"), counts)
        if clean:
            prices[card_id] = clean
    data["prices"] = prices
    data["cleaned"] = counts
    with open(CONDITION_MARKET_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"Condition prices: {len(prices)} cards usable ({counts['capped']} capped at a better condition, "
          f"{counts['outOfOrder']} out of order and {counts['stale']} stale left for staff)")


def update_condition_market(catalog):
    """Refreshes condition_market.json from JustTCG. Never stops the price update: on any
    problem the old file is kept."""
    key = os.environ.get("JUSTTCG_API_KEY")
    if not key:
        print("Condition prices: no JUSTTCG_API_KEY, skipped")
        return
    try:
        with open(CONDITION_MARKET_FILE, encoding="utf-8") as f:
            old = json.load(f)
    except (OSError, ValueError):
        old = {"prices": {}}
    if "raw" not in old:
        # Files from before "raw" was kept: their prices were the raw ones (no report dates)
        old["raw"] = {card_id: {code: {"price": p} for code, p in found.items()}
                      for card_id, found in old.get("prices", {}).items()}
    if old.get("fetchedAt") and old.get("raw"):  # an empty result doesn't count
        age = datetime.now(timezone.utc) - datetime.fromisoformat(old["fetchedAt"])
        if age.total_seconds() < CONDITION_MARKET_EVERY_HOURS * 3600:
            print(f"Condition prices: fetched {age.total_seconds() / 3600:.1f} hours ago, not fetching; re-checking saved prices")
            write_condition_prices(old, catalog)
            return

    wanted = {}  # TCGplayer product ID -> [(card ID, entry)]
    cards = sorted(
        (
            (card_id, entry) for card_id, entry in catalog.items()
            if (entry.get("conditionPricing") or entry.get("overnumbered"))
            and (entry.get("rawMarket") or 0) > CONDITION_MARKET_MIN
        ),
        key=lambda item: -item[1]["rawMarket"],
    )[:CONDITION_MARKET_MAX_CARDS]
    for card_id, entry in cards:
        product = entry_product_key(card_id, entry)
        if product:
            wanted.setdefault(product[0], []).append((card_id, entry))

    ids = list(wanted)
    print(f"Condition prices: looking up {len(cards)} cards over ${CONDITION_MARKET_MIN} ({len(ids)} TCGplayer products)...")
    prices = {}
    # What came back, saved in the file so problems can be seen without the Actions log
    stats = {"cards": len(cards), "products": len(ids), "requests": 0, "returned": 0, "matched": 0, "sample": None}
    problem = None
    try:
        for start in range(0, len(ids), JUSTTCG_BATCH):
            batch = ids[start:start + JUSTTCG_BATCH]
            resp = requests.post(JUSTTCG_URL, json=[{"tcgplayerId": pid} for pid in batch],
                                 headers={"x-api-key": key}, timeout=60)
            stats["requests"] += 1
            if resp.status_code != 200:
                raise RuntimeError(f"JustTCG HTTP {resp.status_code}: {resp.text[:300]}")
            body = resp.json()
            data = body.get("data") or []
            stats["returned"] += len(data)
            if stats["sample"] is None:
                first = data[0] if data else {}
                stats["sample"] = {
                    "responseKeys": sorted(body.keys()),
                    "error": body.get("error") or body.get("errors") or body.get("message"),
                    "cardKeys": sorted(first.keys()),
                    "tcgplayerId": first.get("tcgplayerId"),
                    "asked": batch[:3],
                    "variants": [{k: v.get(k) for k in ("printing", "condition", "price", "language", "priceStatus")}
                                 for v in (first.get("variants") or [])[:8]],
                }
            for card in data:
                for card_id, entry in wanted.get(str(card.get("tcgplayerId")), []):
                    found = pick_condition_prices(card, entry, card_id)
                    if found:
                        prices[card_id] = found
            remaining = (body.get("_metadata") or {}).get("apiRequestsRemaining")
            stats["requestsRemaining"] = remaining
            if remaining is not None and remaining < 5:
                print(f"  JustTCG has {remaining} requests left this period, stopping early")
                break
            time.sleep(JUSTTCG_DELAY)
    except (requests.RequestException, RuntimeError, ValueError) as e:
        problem = str(e)
    stats["matched"] = len(prices)
    print(f"Condition prices: {json.dumps(stats)}")

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if problem or not prices:
        # Keep the old prices and fetch time, so the next run tries again
        old.update({"lastAttempt": now, "lastProblem": problem or "no prices matched", "lastStats": stats})
        print(f"Condition prices: kept the old prices ({problem or 'no prices matched'})")
        write_condition_prices(old, catalog)
        return

    write_condition_prices({
        "fetchedAt": now,
        "source": "JustTCG: TCGplayer market price by condition",
        "lastStats": stats,
        "raw": prices,
    }, catalog)


def normalize_id(card_id):
    """Same as the price page's normalizeId: case and leading zeros don't matter (MEP-023 = mep-23)."""
    return re.sub(r"-0+(?=\d)", "-", str(card_id).strip().lower())


def shard_of(card_id):
    """Which cards/<n>.json a card is in. Must match shardOf in index.html."""
    h = 0
    for ch in normalize_id(card_id):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return h % SHARD_COUNT


def write_card_shards(catalog):
    """The price page's fast path: prices.json split into SHARD_COUNT small files (cards/<n>.json), so a
    customer's scan downloads one ~30 KB file instead of every card. TCGplayer condition prices
    (condition_market.json) are included; staff condition prices change any time, so the page still
    reads condition_prices.json (small) live."""
    try:
        with open(CONDITION_MARKET_FILE, encoding="utf-8") as f:
            market = json.load(f)
    except (OSError, ValueError):
        market = {}
    market_prices = market.get("prices") or {}
    shards = [{} for _ in range(SHARD_COUNT)]
    for card_id, entry in catalog.items():
        if card_id in market_prices:
            entry = dict(entry, conditionMarket=market_prices[card_id], conditionMarketAt=market.get("fetchedAt"))
        shards[shard_of(card_id)][card_id] = entry
    os.makedirs(SHARD_DIR, exist_ok=True)
    for n, cards in enumerate(shards):
        with open(os.path.join(SHARD_DIR, f"{n}.json"), "w", encoding="utf-8") as f:
            json.dump(cards, f, separators=(",", ":"))
    print(f"Wrote {SHARD_COUNT} card files to {SHARD_DIR}/ (largest {max(len(s) for s in shards)} cards)")


def read_catalog():
    """Every game's cards, prices.json and the GAME_FILES merged into one catalog."""
    catalog = {}
    for name in [CATALOG_FILE] + list(GAME_FILES.values()):
        try:
            with open(name, encoding="utf-8") as f:
                catalog.update(json.load(f))
        except (OSError, ValueError):
            pass
    return catalog


def write_catalog(catalog):
    """Splits the catalog into its game files and writes them. Returns { file: { cards, changedAt } }:
    changedAt only moves when a file's contents changed, so the shop app downloads only the files that did."""
    try:
        with open(VERSION_FILE, encoding="utf-8") as f:
            before = json.load(f).get("files") or {}
    except (OSError, ValueError):
        before = {}
    now = datetime.now(timezone.utc).isoformat()
    split = {name: {} for name in [CATALOG_FILE] + list(GAME_FILES.values())}
    for card_id, entry in catalog.items():
        split[GAME_FILES.get(entry.get("game"), CATALOG_FILE)][card_id] = entry
    files = {}
    for name, cards in split.items():
        text = json.dumps(cards, separators=(",", ":"))  # no indenting: about half the size to download
        try:
            with open(name, encoding="utf-8") as f:
                same = f.read() == text
        except OSError:
            same = False
        if not same:
            with open(name, "w", encoding="utf-8") as f:
                f.write(text)
        changed_at = (before.get(name) or {}).get("changedAt") if same else None
        files[name] = {"cards": len(cards), "changedAt": changed_at or now}
    return files


def build_price_catalog(part):
    updates = {}

    if part in ("all", "1"):
        for rarity in RARITY_QUERIES:
            fetch_rarity(updates, rarity)
        fetch_singles(updates)
        for set_id in ALL_CARD_SETS:
            fetch_all_cards(updates, set_id)
        for group in PROMO_GROUPS:
            fetch_promo_group(updates, group)

    if part in ("all", "2"):
        for set_id in ALL_HOLO_SETS:
            fetch_all_holos(updates, set_id)

    if part in ("all", "2", "3", "4"):
        fetch_overnumbered(updates, part)

    if part in ("all", "4"):
        fetch_swsh_era(updates)

    # A single part only replaces its own cards and keeps everything else as it was
    catalog = read_catalog() if part != "all" else {}

    # The steps below look at the database as it is (cards added before stay; printings listed
    # another way, or as stocked cards, are skipped)
    previous = read_catalog()
    existing = dict(previous)
    existing.update(catalog)
    existing.update(updates)

    if part in ("all", "2"):
        fetch_artists(updates, existing)

    if part in ("all", "3"):
        # Jumbo cards, and SV / ME commons, uncommons and rares over CHEAP_MIN_PRICE
        groups = get_json(f"{TCGCSV_BASE}/groups", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(REQUEST_DELAY)
        fetch_jumbo(updates, groups)
        fetch_sv_me_commons(updates, groups, existing)

    if part in ("all", "6"):
        # Stamped cards and pattern variants, from every English Pokémon group
        groups = get_json(f"{TCGCSV_BASE}/groups", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(REQUEST_DELAY)
        merged = dict(existing)
        merged.update(updates)
        fetch_variants(updates, groups, merged)

    if part in ("all", "7"):
        # Sealed products from Sword & Shield on
        groups = get_json(f"{TCGCSV_BASE}/groups", headers=TCGCSV_HEADERS).get("results", [])
        time.sleep(REQUEST_DELAY)
        fetch_sealed(updates, groups, previous)

    stock = load_stock()
    if part in ("all", "5"):
        # Japanese cards and stocked cards are rebuilt every run (cards that left the
        # list drop out, and stocked cards now covered elsewhere aren't listed twice).
        # Sword & Shield era cards also have tcg- IDs but belong to part 4 ("group"): kept.
        catalog = {k: v for k, v in catalog.items() if not (k.startswith("jp-") or is_stock_entry(k, v))}
        fetch_japanese(updates)
        existing = dict(catalog)
        existing.update(updates)
        existing = {k: v for k, v in existing.items() if not is_stock_entry(k, v)}
        fetch_stock(updates, stock, existing)

    catalog.update(updates)
    mark_stocked(catalog, stock)
    add_bulk_tags(catalog)

    # Save to prices.json and the other games' files
    files = write_catalog(catalog)

    update_history(catalog)
    if part in ("all", "5"):
        update_condition_market(catalog)

    write_card_shards(catalog)
    # Last, so nobody sees a new version before its files are written. The shop app checks this tiny
    # file often and only downloads the big prices.json when it changed (also after a run started by
    # hand during the day); the price page uses it to know when its saved shards are out of date.
    # "cards" is prices.json's count (installed apps check a download against it), "total" every game's,
    # "files" each game file's count and when it last changed.
    with open(VERSION_FILE, "w", encoding="utf-8") as f:
        json.dump({"updatedAt": datetime.now(timezone.utc).isoformat(), "part": part,
                   "cards": files[CATALOG_FILE]["cards"], "total": len(catalog), "files": files}, f)

    print(f"Successfully updated {len(updates)} cards (part {part}); the database has {len(catalog)} cards (" +
          ", ".join(f"{name} {info['cards']}" for name, info in files.items()) + ").")

if __name__ == "__main__":
    # Usage: python update_prices.py [all|1|2|3|4|5]   (defaults to all)
    part = sys.argv[1] if len(sys.argv) > 1 else "all"
    if part != "all" and part not in PARTS:
        print(f"Unknown part '{part}'. Use all, {', '.join(PARTS)}.")
        sys.exit(2)
    print(f"Running part {part}: {PARTS.get(part, 'everything')}")

    try:
        build_price_catalog(part)
    except RuntimeError as e:
        # Leave the existing prices.json untouched rather than publish a partial one
        print(f"Price update stopped, prices.json was not changed: {e}")
        sys.exit(1)
