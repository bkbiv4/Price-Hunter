"""Read-only checks of configured grading tiers against official websites."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any, Callable

import requests

from grading import GRADING_SERVICE_TIERS


OFFICIAL_GRADING_URLS = {
    "PSA": "https://www.psacard.com/services",
    "BGS": "https://www.beckett.com/grading",
    "CGC": "https://www.cgccards.com/submit/services-fees/cgc-grading/?view=cards",
}


def _page_text(content: str) -> str:
    content = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", content, flags=re.I | re.S)
    content = re.sub(r"<[^>]+>", " ", content)
    return re.sub(r"\s+", " ", html.unescape(content)).casefold()


def _number_tokens(value: str) -> list[str]:
    return re.findall(r"\d+(?:\.\d+)?", value.replace(",", ""))


def _tier_segment(page: str, tier_name: str, all_names: list[str]) -> str:
    pattern = rf"(?<!\w){re.escape(tier_name.casefold())}(?!\w)"
    starts = [match.start() for match in re.finditer(pattern, page)]
    candidates = []
    for start in starts:
        ends = []
        for name in all_names:
            match = re.search(
                rf"(?<!\w){re.escape(name.casefold())}(?!\w)",
                page[start + len(tier_name):],
            )
            ends.append(start + len(tier_name) + match.start() if match else -1)
        ends = [position for position in ends if position > start]
        segment = page[start:min(ends) if ends else start + 1200]
        score = segment.count("$") * 2 + segment.count("days") + segment.count("per card")
        candidates.append((score, segment))
    return max(candidates, default=(0, ""), key=lambda item: item[0])[1]


def _fee_present(segment: str, fee: float) -> bool:
    forms = {f"${fee:,.2f}", f"${fee:g}", f"{fee:,.2f} per card", f"{fee:g} per card"}
    return any(form.casefold() in segment for form in forms)


def verify_grading_services(
    fetch: Callable[..., Any] = requests.get,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Compare local planning values with visible text on each official page."""
    companies: dict[str, Any] = {}
    for company, tiers in GRADING_SERVICE_TIERS.items():
        url = OFFICIAL_GRADING_URLS[company]
        try:
            response = fetch(url, timeout=timeout, headers={"User-Agent": "PriceHunter/1.0 tier-verifier"})
            response.raise_for_status()
            page = _page_text(response.text)
            names = [str(tier["name"]) for tier in tiers]
            tier_results = []
            for tier in tiers:
                segment = _tier_segment(page, str(tier["name"]), names)
                checks = {
                    "tier": bool(segment),
                    "fee": _fee_present(segment, float(tier["fee"])),
                    "turnaround": all(token in segment for token in _number_tokens(str(tier["turnaround"]))),
                }
                limit = tier.get("max_declared_value")
                checks["value_limit"] = limit is None or any(
                    token in segment for token in {
                        f"{float(limit):,.0f}", f"{float(limit):.0f}", f"{float(limit) / 1000:g},000"
                    }
                )
                tier_results.append({
                    "tier": tier["name"],
                    "status": "Verified" if all(checks.values()) else "Review",
                    "checks": checks,
                })
            companies[company] = {
                "status": "Verified" if all(row["status"] == "Verified" for row in tier_results) else "Review",
                "source": url,
                "tiers": tier_results,
                "error": "",
            }
        except requests.RequestException as exc:
            companies[company] = {
                "status": "Unavailable", "source": url, "tiers": [], "error": str(exc)
            }
    return {"checked_at": datetime.now(timezone.utc).isoformat(), "companies": companies}
