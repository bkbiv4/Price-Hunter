"""Grading opportunity calculations."""

from __future__ import annotations

from typing import Any


def grading_opportunity(
    card: dict[str, Any],
    grading_cost: float,
    selling_cost_rate: float,
    grade_probabilities: dict[str, float],
) -> dict[str, Any]:
    """Estimate grading outcomes for one inventory card."""
    quantity = max(1, int(card.get("quantity") or 1))
    cost_basis = float(card.get("cost") or 0)
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
    expected_profit = profit(expected_value)
    raw_profit = profit(raw_value) + grading_cost
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
        "grading_cost": round(grading_cost, 2),
        "break_even_grade": break_even,
        "grade_8_profit": profits["8 / 8.5"],
        "grade_9_profit": profits["9"],
        "psa_10_profit": profits["10"],
        "expected_value": round(expected_value, 2),
        "expected_profit": expected_profit,
        "expected_uplift_vs_raw": round(expected_profit - raw_profit, 2),
    }
