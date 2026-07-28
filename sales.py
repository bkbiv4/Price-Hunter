"""Sales documents and helpers."""

from __future__ import annotations

from typing import Any


def packing_slip_text(sale: dict[str, Any], items: list[dict[str, Any]]) -> str:
    lines = [
        "PRICE HUNTER PACKING SLIP",
        "",
        f"Order: {sale.get('order_number') or sale.get('id', '')}",
        f"Sale date: {sale.get('sale_date') or ''}",
        f"Marketplace: {sale.get('marketplace') or ''}",
        f"Buyer: {sale.get('buyer') or ''}",
        "",
        "ITEMS",
    ]
    if items:
        for item in items:
            lines.append(
                f"{int(item.get('quantity') or 1)} x "
                f"{item.get('card_name') or 'Card'} [{item.get('card_sku') or ''}]"
            )
    else:
        lines.append(f"{int(sale.get('quantity') or 1)} x {sale.get('title') or 'Card'}")
    lines.extend([
        "",
        f"Items: ${float(sale.get('item_subtotal') or 0):,.2f}",
        f"Shipping: ${float(sale.get('shipping_charged') or 0):,.2f}",
        "",
        "Thank you for your purchase.",
    ])
    return "\n".join(lines)
