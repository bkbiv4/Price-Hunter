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
