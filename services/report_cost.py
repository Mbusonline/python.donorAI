"""
Report cost: token totals and monetary pricing from tbl_model.input_pricing/output_pricing.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from database.connection import connect

# (input_rate_per_token, output_rate_per_token)
Rates = Tuple[float, float]

_model_cache: Optional[Dict[str, Any]] = None

# Your DB stores pricing as "per 1M tokens".
PRICING_UNIT_TOKENS = 1_000_000.0


def _norm_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _load_model_cache() -> Dict[str, Any]:
    global _model_cache
    if _model_cache is not None:
        return _model_cache

    by_id: Dict[str, Rates] = {}
    by_key: Dict[str, Rates] = {}

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT model_id::text, title, provider, input_pricing, output_pricing
                FROM tbl_model
                WHERE is_active = TRUE
                """
            )
            rows = cur.fetchall()

    for model_id, title, provider, input_pricing, output_pricing in rows:
        try:
            inp = float(input_pricing or 0.0) / PRICING_UNIT_TOKENS
        except (TypeError, ValueError):
            inp = 0.0
        try:
            out = float(output_pricing or 0.0) / PRICING_UNIT_TOKENS
        except (TypeError, ValueError):
            out = 0.0
        rates = (inp, out)
        mid = str(model_id)
        by_id[mid] = rates
        prov = _norm_key(str(provider or ""))
        if prov in ("gemini",):
            prov = "google"
        tit = _norm_key(str(title or ""))
        if tit:
            by_key[f"{prov}:{tit}"] = rates
        if tit:
            by_key[tit] = rates

    _model_cache = {"by_id": by_id, "by_key": by_key}
    return _model_cache


def clear_model_pricing_cache() -> None:
    """Reset cached tbl_model pricing (e.g. after tests)."""
    global _model_cache
    _model_cache = None


def _resolve_rates(
    *,
    model_id: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> Rates:
    cache = _load_model_cache()
    if model_id:
        rates = cache["by_id"].get(str(model_id))
        if rates is not None:
            return rates

    prov = _norm_key(str(provider or ""))
    if prov in ("gemini",):
        prov = "google"
    mod = _norm_key(str(model or ""))
    if mod:
        hit = cache["by_key"].get(f"{prov}:{mod}")
        if hit is not None:
            return hit
        hit = cache["by_key"].get(mod)
        if hit is not None:
            return hit

    return 0.0, 0.0


def line_item_cost(usage: Dict[str, Any]) -> float:
    """Cost for one API usage row using its model's tbl_model.pricing."""
    inp_r, out_r = _resolve_rates(
        model_id=usage.get("model_id"),
        provider=usage.get("provider"),
        model=usage.get("model"),
    )
    i = usage.get("input_tokens")
    o = usage.get("output_tokens")
    tin = int(i) if isinstance(i, int) else 0
    tout = int(o) if isinstance(o, int) else 0
    return tin * inp_r + tout * out_r


def compute_report_cost(
    breakdown: List[Dict[str, Any]],
) -> Tuple[int, int, float, List[Dict[str, Any]]]:
    """
    Sum tokens and monetary cost across all usage rows.

    Returns (input_token_count, output_token_count, total_pricing, enriched_breakdown).
    Each breakdown row gains computed_cost when tokens are present.
    """
    tin = 0
    tout = 0
    total = 0.0
    enriched: List[Dict[str, Any]] = []

    for raw in breakdown:
        row = dict(raw)
        i = row.get("input_tokens")
        o = row.get("output_tokens")
        if isinstance(i, int):
            tin += i
        if isinstance(o, int):
            tout += o
        cost = line_item_cost(row)
        if cost:
            row["computed_cost"] = round(cost, 8)
            total += cost
        enriched.append(row)

    return tin, tout, round(total, 8), enriched
