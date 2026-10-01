"""Grading opportunity calculations."""

from __future__ import annotations

from typing import Any


PSA_SERVICE_TIERS = [
    {
        "name": "Regular",
        "fee": 79.99,
        "max_declared_value": 1500.0,
        "turnaround": "70 - 80 business days",
    },
    {
        "name": "Express",
        "fee": 149.0,
        "max_declared_value": 2500.0,
        "turnaround": "20 - 30 business days",
    },
    {
        "name": "Super Express",
        "fee": 349.0,
        "max_declared_value": 5000.0,
        "turnaround": "10 - 15 business days",
    },
    {
        "name": "Walk-Through",
        "fee": 599.0,
        "max_declared_value": 10000.0,
        "turnaround": "7 - 10 business days",
    },
]

# Planning defaults, deliberately kept in one place so they can be replaced by a
# live service-level feed later. Availability is not implied by this table.
GRADING_SERVICE_TIERS = {
    "PSA": PSA_SERVICE_TIERS,
    "BGS": [
        {"name": "Base", "fee": 17.95, "max_declared_value": None, "turnaround": "75+ business days"},
        {"name": "Standard", "fee": 34.95, "max_declared_value": None, "turnaround": "45 business days"},
        {"name": "Express", "fee": 79.95, "max_declared_value": None, "turnaround": "15 business days"},
        {"name": "Priority", "fee": 124.95, "max_declared_value": None, "turnaround": "5 business days"},
    ],
    "CGC": [
        {"name": "Bulk", "fee": 17.0, "max_declared_value": 500.0, "turnaround": "150 working days"},
        {"name": "Economy", "fee": 20.0, "max_declared_value": 1000.0, "turnaround": "90 working days"},
        {"name": "Standard", "fee": 55.0, "max_declared_value": 3000.0, "turnaround": "10 working days"},
        {"name": "Express", "fee": 100.0, "max_declared_value": 10000.0, "turnaround": "5 working days"},
        {"name": "WalkThrough", "fee": 300.0, "max_declared_value": 100000.0, "turnaround": "2 working days"},
    ],
}


def grader_strategy(
    raw_value: float,
    psa_10_value: float,
    copy_quality: str,
    black_label_multiplier: float = 3.0,
    bgs_black_10_value: float = 0.0,
) -> dict[str, Any]:
    """Return transparent company/strategy suggestions without inventing comps."""
    raw_value = float(raw_value or 0)
    psa_10_value = float(psa_10_value or 0)
    multiplier = max(1.0, float(black_label_multiplier or 1))
    actual_black_label = float(bgs_black_10_value or 0)
    jackpot = actual_black_label or (round(psa_10_value * multiplier, 2) if psa_10_value else 0.0)
    ten_multiple = psa_10_value / raw_value if raw_value > 0 else 0.0

    if not psa_10_value:
        action, reason = "Needs comps", "Fetch a PSA 10 value before choosing a grader."
    elif raw_value < 25 and ten_multiple < 4:
        action, reason = "Sell raw", "Low raw value and limited PSA 10 multiplier."
    elif psa_10_value <= raw_value * 1.35:
        action, reason = "Sell raw", "PSA 10 upside is too close to raw value."
    elif copy_quality == "Exceptional / flawless candidate" and jackpot >= psa_10_value * 2:
        action, reason = "BGS jackpot swing", "Exceptional copy and large modeled Black Label upside."
    elif copy_quality == "Visible flaw / likely 8 or lower":
        action, reason = "Sell raw", "The likely grade does not justify the downside."
    else:
        action, reason = "PSA resale play", "PSA 10 offers the clearest measured upside and liquidity path."

    return {
        "recommended_path": action,
        "recommendation_reason": reason,
        "best_for_resale": "PSA",
        "best_for_speed": "BGS Priority",
        "best_for_budget": "BGS Base",
        "jackpot_path": "BGS Black Label",
        "modeled_black_label_value": jackpot,
        "black_label_value_source": "SportsCardsPro" if actual_black_label else "Modeled scenario",
        "psa_10_multiple": round(ten_multiple, 2),
        "other_grader_comps_status": "Not fetched yet",
    }


def psa_declared_value(card: dict[str, Any], basis: str) -> float:
    values = {
        "PSA 10 value": float(card.get("psa_10_price") or 0),
        "Grade 9 value": float(card.get("graded_9_price") or 0),
        "Expected value": 0.0,
        "Highest available graded value": max(
            float(card.get("graded_8_price") or 0),
            float(card.get("graded_9_price") or 0),
            float(card.get("psa_10_price") or 0),
        ),
    }
    return values.get(basis, values["PSA 10 value"])


def psa_tier_for_value(declared_value: float) -> dict[str, Any] | None:
    for tier in PSA_SERVICE_TIERS:
        if declared_value <= float(tier["max_declared_value"]):
            return tier
    return None


def grading_opportunity(
    card: dict[str, Any],
    grading_cost: float,
    selling_cost_rate: float,
    grade_probabilities: dict[str, float],
    grading_tier: dict[str, Any] | None = None,
    declared_value: float | None = None,
) -> dict[str, Any]:
    """Estimate grading outcomes for one inventory card."""
    quantity = max(1, int(card.get("quantity") or 1))
    cost_basis = float(card.get("cost") or 0) + float(card.get("grading_cost") or 0)
    raw_value = float(card.get("market_price") or 0)
    prices = {
        "8 / 8.5": float(card.get("graded_8_price") or 0),
        "9": float(card.get("graded_9_price") or 0),
        "10": float(card.get("psa_10_price") or 0),
    }
    investment = cost_basis + grading_cost

    def profit(price: float) -> float:
        return round(price * (1 - selling_cost_rate) - investment, 2)

    profits = {grade: profit(price) for grade, price in prices.items()}
    break_even = next(
        (grade for grade in ("8 / 8.5", "9", "10") if prices[grade] > 0 and profits[grade] >= 0),
        "Above 10",
    )
    expected_value = sum(
        prices[grade] * float(grade_probabilities.get(grade, 0))
        for grade in prices
    )
    if declared_value is None:
        declared_value = float(prices["10"] or expected_value or raw_value)
    expected_profit = profit(expected_value)
    raw_profit = profit(raw_value) + grading_cost
    grading_decision = (
        "Grade"
        if expected_profit > 0 and expected_profit > raw_profit
        else "Hold"
    )
    return {
        "id": int(card["id"]),
        "sku": card.get("sku", ""),
        "card_name": card.get("card_name", ""),
        "set_name": card.get("set_name", ""),
        "quantity": quantity,
        "cost_basis": round(cost_basis, 2),
        "raw_value": round(raw_value, 2),
        "grade_8_value": round(prices["8 / 8.5"], 2),
        "grade_9_value": round(prices["9"], 2),
        "psa_10_value": round(prices["10"], 2),
        "declared_value": round(float(declared_value), 2),
        "recommended_tier": grading_tier["name"] if grading_tier else "Manual",
        "tier_fee": round(float(grading_tier["fee"]), 2) if grading_tier else round(grading_cost, 2),
        "tier_max_value": round(float(grading_tier["max_declared_value"]), 2) if grading_tier else None,
        "tier_turnaround": grading_tier["turnaround"] if grading_tier else "",
        "tier_covered": (
            float(declared_value) <= float(grading_tier["max_declared_value"])
            if grading_tier else True
        ),
        "grading_cost": round(grading_cost, 2),
        "grading_decision": grading_decision,
        "break_even_grade": break_even,
        "grade_8_profit": profits["8 / 8.5"],
        "grade_9_profit": profits["9"],
        "psa_10_profit": profits["10"],
        "expected_value": round(expected_value, 2),
        "expected_profit": expected_profit,
        "expected_uplift_vs_raw": round(expected_profit - raw_profit, 2),
    }
