import re
import time
import json
import math
import requests

PAGE_SIZE = 250

# Rarities pulled from the Pokemon TCG API (Scarlet & Violet + Mega Evolution only)
RARITY_QUERIES = ["Double Rare", "ACE SPEC Rare"]

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


def fetch_rarity(catalog, rarity):
    page = 1
    print(f"Fetching live prices for all {rarity} cards...")

    while True:
        query = f'rarity:"{rarity}"'
        url = f"https://api.carddex.dev/compat/pokemontcg/v2/cards?q={query}&page={page}&pageSize={PAGE_SIZE}&select=id,name,number,set,tcgplayer,images,rarity"

        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                print(f"Failed page {page}: {resp.status_code}")
                break

            data = resp.json()
            cards = data.get("data", [])
            if not cards:
                break

            for card in cards:
                series = card.get("set", {}).get("series", "")
                if "Scarlet" not in series and "Mega" not in series:
                    continue

                card_id = card.get("id")
                prices = card.get("tcgplayer", {}).get("prices", {})

                raw_market = None
                for variant in ["holofoil", "normal", "reverseHolofoil"]:
                    if variant in prices and prices[variant].get("market") is not None:
                        raw_market = prices[variant]["market"]
                        break

                store_price = calculate_store_price(raw_market)

                catalog[card_id] = {
                    "id": card_id,
                    "name": card.get("name"),
                    "setName": card.get("set", {}).get("name", "Scarlet & Violet"),
                    "number": card.get("number", "???"),
                    "printedTotal": card.get("set", {}).get("printedTotal", ""),
                    "rarity": card.get("rarity", rarity),
                    "price": store_price,
                    "rawMarket": raw_market,
                    "image": card.get("images", {}).get("small", ""),
                    "tcgUrl": card.get("tcgplayer", {}).get("url", "https://www.tcgplayer.com")
                }

            total_count = data.get("totalCount", 0)
            print(f"Synced page {page} ({len(catalog)} cards indexed / {total_count} total {rarity} cards)")

            if page * PAGE_SIZE >= total_count:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            print(f"Error on page {page}: {e}")
            break


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
    try:
        products = requests.get(f"{TCGCSV_BASE}/{group['groupId']}/products", headers=TCGCSV_HEADERS, timeout=30).json().get("results", [])
        price_rows = requests.get(f"{TCGCSV_BASE}/{group['groupId']}/prices", headers=TCGCSV_HEADERS, timeout=30).json().get("results", [])
    except Exception as e:
        print(f"Error fetching {group['setName']}: {e}")
        return

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


def build_price_catalog():
    catalog = {}

    for rarity in RARITY_QUERIES:
        fetch_rarity(catalog, rarity)

    for group in PROMO_GROUPS:
        fetch_promo_group(catalog, group)

    # Save to prices.json
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print(f"Successfully generated prices.json with {len(catalog)} cards.")

if __name__ == "__main__":
    build_price_catalog()
