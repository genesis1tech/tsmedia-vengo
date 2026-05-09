"""Heuristics for recyclable container inference."""

from __future__ import annotations

from .models import ProductCandidate


def infer_container(candidate: ProductCandidate) -> tuple[str, float]:
    text = " ".join(
        value
        for value in (
            candidate.product_name,
            candidate.product_desc,
            candidate.product_category,
            str(candidate.raw.get("packageWeight") or ""),
            str(candidate.raw.get("householdServingFullText") or ""),
            str(candidate.raw.get("packaging") or ""),
        )
        if value
    ).lower()

    if " can" in f" {text}" or text.endswith("can") or "aluminum" in text:
        return "can", 0.9
    if "glass bottle" in text:
        return "glass_bottle", 0.9
    if "plastic bottle" in text or "pet bottle" in text:
        return "plastic_bottle", 0.9
    if "carton" in text or "tetra" in text:
        return "carton", 0.75
    if any(word in text for word in ("sparkling water", "soda", "beverage", "drink")):
        return "can", 0.65
    if candidate.container_type:
        return candidate.container_type, candidate.container_confidence or 0.5
    return "unknown", 0.0
