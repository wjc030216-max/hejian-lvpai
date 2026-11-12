"""Server-authoritative prices for one-, two-, and three-person templates."""
from __future__ import annotations

import os


TIER_PRICES = {1: 990, 2: 1690, 3: 1990}


def flat_override():
    raw = os.getenv('AITRAVEL_AIVIDEO_PRICE_FEN', '').strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        raise ValueError('AITRAVEL_AIVIDEO_PRICE_FEN 必须为整数分') from None
    if value <= 0:
        raise ValueError('AITRAVEL_AIVIDEO_PRICE_FEN 必须大于 0')
    return value


def price_for_actor_count(actor_count):
    try:
        count = int(actor_count)
    except (TypeError, ValueError):
        raise ValueError('模板人数无效，无法计算价格') from None
    if count not in TIER_PRICES:
        raise ValueError('当前只支持 1 至 3 人模板')
    return flat_override() or TIER_PRICES[count]


def price_for_template(template):
    actor_count = template.get('actor_count')
    if actor_count is None:
        roles = template.get('character_settings') or template.get('roles') or []
        actor_count = len(roles)
    return price_for_actor_count(actor_count)


def public_pricing():
    override = flat_override()
    standard = {str(count): price for count, price in TIER_PRICES.items()}
    charge = {str(count): override or price for count, price in TIER_PRICES.items()}
    return {
        'pricing_mode': 'flat' if override else 'tier',
        'amount_fen': override or 0,
        # Keep price_tiers as the actual charge for older clients.
        'price_tiers': charge,
        'standard_price_tiers': standard,
        'charge_price_tiers': charge,
    }
