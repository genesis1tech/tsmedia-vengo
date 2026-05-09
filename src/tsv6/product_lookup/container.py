"""Heuristics for recyclable container inference."""

from __future__ import annotations

from .models import ProductCandidate


def infer_container(candidate: ProductCandidate) -> tuple[str, float]:
    text = " ".join(
        value
        for value in (
            candidate.product_brand,
            candidate.product_name,
            candidate.product_desc,
            candidate.product_category,
            str(candidate.raw.get("packageWeight") or ""),
            str(candidate.raw.get("householdServingFullText") or ""),
            str(candidate.raw.get("packaging") or ""),
            str(candidate.raw.get("packagings") or ""),
            str(candidate.raw.get("categories_tags") or ""),
            str(candidate.raw.get("_keywords") or ""),
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
    if "energy-drink" in text or "energy drink" in text:
        return "can", 0.75
    if any(word in text for word in ("sparkling water", "soda", "beverage", "drink")):
        return "can", 0.65
    if any(brand in text for brand in _COMMON_CANNED_BEVERAGE_BRANDS):
        return "can", 0.55
    if candidate.container_type:
        return candidate.container_type, candidate.container_confidence or 0.5
    return "unclassified", 0.1


_COMMON_CANNED_BEVERAGE_BRANDS = {
    "alani",
    "bang",
    "celsius",
    "ghost",
    "monster",
    "red bull",
    "reign",
    "rockstar",
    "starbucks",
}
