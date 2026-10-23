import re
import sys
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

# The daily update runs in 4 parts, 30 minutes apart, so no single run sends
# enough requests to get rate limited. Each part only replaces its own cards.
PARTS = {
    "1": "Double Rares, ACE SPECs, single cards, 30th Classic Collection and promos",
    "2": "151 holos and reverse holos, plus overnumbered cards from the first half of Scarlet & Violet",
    "3": "Overnumbered cards from the second half of Scarlet & Violet",
    "4": "Overnumbered cards from Mega Evolution",
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

    # A single part only replaces its own cards and keeps everything else as it was
    catalog = {}
    if part != "all":
        try:
            with open("prices.json", encoding="utf-8") as f:
                catalog = json.load(f)
        except (OSError, ValueError):
            catalog = {}
    catalog.update(updates)

    # Save to prices.json
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print(f"Successfully updated {len(updates)} cards (part {part}); prices.json has {len(catalog)} cards.")

if __name__ == "__main__":
    # Usage: python update_prices.py [all|1|2|3|4]   (defaults to all)
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
