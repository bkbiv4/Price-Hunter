"""Federated search links for major card marketplaces and online stores."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote_plus


@dataclass(frozen=True)
class Seller:
    name: str
    focus: str
    domain: str
    search_url: str = ""


SELLERS = (
    Seller("TCGplayer", "TCG singles", "tcgplayer.com", "https://www.tcgplayer.com/search/all/product?q={query}"),
    Seller("eBay", "All cards", "ebay.com", "https://www.ebay.com/sch/i.html?_nkw={query}"),
    Seller("Cardmarket", "TCG singles (Europe)", "cardmarket.com"),
    Seller("Troll and Toad", "TCG singles", "trollandtoad.com", "https://www.trollandtoad.com/category.php?selected-cat=0&search-words={query}"),
    Seller("Card Kingdom", "MTG singles", "cardkingdom.com", "https://www.cardkingdom.com/catalog/search?search=header&filter%5Bname%5D={query}"),
    Seller("Star City Games", "MTG and TCG", "starcitygames.com", "https://starcitygames.com/search/?search_query={query}"),
    Seller("CoolStuffInc", "MTG and TCG", "coolstuffinc.com", "https://www.coolstuffinc.com/main_search.php?pa=searchOnName&q={query}"),
    Seller("CardTrader", "TCG singles", "cardtrader.com", "https://www.cardtrader.com/search?query={query}"),
    Seller("COMC", "Sports and TCG", "comc.com"),
    Seller("Beckett Marketplace", "Sports and TCG", "beckett.com"),
    Seller("Fanatics Collect", "Graded and sports", "fanaticscollect.com"),
    Seller("MySlabs", "Graded cards", "myslabs.com", "https://myslabs.com/search/?q={query}"),
    Seller("Dave & Adam's", "Sealed and singles", "dacardworld.com", "https://www.dacardworld.com/search?Search={query}"),
    Seller("Steel City Collectibles", "Sealed and sports", "steelcitycollectibles.com", "https://www.steelcitycollectibles.com/search?q={query}"),
    Seller("Blowout Cards", "Sealed and sports", "blowoutcards.com", "https://www.blowoutcards.com/catalogsearch/result/?q={query}"),
    Seller("Game Nerdz", "Sealed TCG", "gamenerdz.com", "https://www.gamenerdz.com/search.php?search_query={query}"),
    Seller("Smoke and Mirrors Hobby", "Pokemon and TCG", "smokeandmirrorshobby.com", "https://smokeandmirrorshobby.com/search?q={query}"),
    Seller("Forge and Fire Gaming", "Pokemon and TCG", "forgeandfiregaming.com", "https://forgeandfiregaming.com/search?q={query}"),
    Seller("Collector Store", "Pokemon and TCG", "collectorstore.com", "https://collectorstore.com/search?type=product&q={query}"),
    Seller("Safari Zone Collectibles", "Pokemon and TCG", "safari-zone.com", "https://safari-zone.com/search?q={query}"),
)


def inventory_search_query(card: dict[str, Any]) -> str:
    """Build a seller-friendly search phrase from an inventory card."""
    name = str(card.get("card_name") or "").strip()
    number = str(card.get("card_number") or "").strip()
    parts = [name, str(card.get("set_name") or "").strip()]
    if number and number.casefold() not in name.casefold():
        parts.append(number)
    if str(card.get("condition") or "").casefold() == "graded":
        parts.extend((str(card.get("grader") or "").strip(), str(card.get("grade") or "").strip()))
    return " ".join(part for part in parts if part)


def seller_search_url(seller: Seller, query: str) -> tuple[str, str]:
    encoded = quote_plus(query.strip())
    if seller.search_url:
        return seller.search_url.format(query=encoded), "Direct seller search"
    scoped = quote_plus(f"site:{seller.domain} {query.strip()}")
    return f"https://www.google.com/search?q={scoped}", "Site-scoped web search"


def seller_search_rows(query: str, seller_names: list[str] | None = None) -> list[dict[str, str]]:
    selected = set(seller_names or [seller.name for seller in SELLERS])
    rows = []
    for seller in SELLERS:
        if seller.name not in selected:
            continue
        url, method = seller_search_url(seller, query)
        rows.append({"Seller": seller.name, "Focus": seller.focus, "Method": method, "Search": url})
    return rows
