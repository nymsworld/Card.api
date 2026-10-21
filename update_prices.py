import time
import json
import math
import requests

PAGE_SIZE = 250

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

def build_price_catalog():
    catalog = {}
    page = 1
    print("Fetching live prices for all Double Rares...")

    while True:
        query = 'rarity:"Double Rare"'
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
                    "rarity": card.get("rarity", "Double Rare"),
                    "price": store_price,
                    "rawMarket": raw_market,
                    "image": card.get("images", {}).get("small", ""),
                    "tcgUrl": card.get("tcgplayer", {}).get("url", "https://www.tcgplayer.com")
                }

            total_count = data.get("totalCount", 0)
            print(f"Synced page {page} ({len(catalog)} cards indexed / {total_count} total cards)")

            if page * PAGE_SIZE >= total_count:
                break

            page += 1
            time.sleep(0.5)

        except Exception as e:
            print(f"Error on page {page}: {e}")
            break

    # Save to prices.json
    with open("prices.json", "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2)

    print(f"Successfully generated prices.json with {len(catalog)} cards.")

if __name__ == "__main__":
    build_price_catalog()
