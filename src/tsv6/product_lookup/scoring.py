"""Candidate scoring and merge logic for dry-run product lookup."""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from .barcode import normalize_barcode
from .container import infer_container
from .models import LookupResult, ProductCandidate


def score_candidates(barcode: str, candidates: list[ProductCandidate]) -> LookupResult:
    normalized = normalize_barcode(barcode)
    ranked = sorted(candidates, key=lambda candidate: candidate.source_confidence, reverse=True)
    if not ranked:
        return LookupResult(
            barcode=normalized.get("upc12") or normalized["digits"],
            ean13=normalized.get("ean13"),
            product_brand=None,
            product_name=None,
            product_url=None,
            product_desc=None,
            product_category=None,
            product_image_original=None,
            container_type="unknown",
            container_confidence=0.0,
            lookup_confidence=0.0,
            data_source="none",
            cache_decision="no_match",
            evidence=[],
            conflicts=[],
            candidates=[],
        )

    primary_group, conflicts = _select_primary_group(ranked)
    primary = primary_group[0]
    merged = _merge_group(primary_group)
    support_bonus = min(0.12, 0.04 * max(0, len(primary_group) - 1))
    conflict_penalty = min(0.12, 0.04 * len(conflicts))
    confidence = max(0.0, min(0.98, primary.source_confidence + support_bonus - conflict_penalty))
    container_type, container_confidence = infer_container(merged)

    if confidence >= 0.75 and (merged.product_name or merged.product_brand):
        decision = "cacheable"
    elif confidence >= 0.55 and (merged.product_name or merged.product_brand):
        decision = "review"
    else:
        decision = "no_match"

    data_sources = "+".join(dict.fromkeys(candidate.source for candidate in primary_group))
    evidence = []
    for candidate in primary_group:
        evidence.extend(candidate.evidence)

    return LookupResult(
        barcode=normalized.get("upc12") or normalized["digits"],
        ean13=normalized.get("ean13"),
        product_brand=merged.product_brand,
        product_name=merged.product_name,
        product_url=merged.product_url,
        product_desc=merged.product_desc,
        product_category=merged.product_category,
        product_image_original=merged.product_image_original,
        container_type=container_type,
        container_confidence=container_confidence,
        lookup_confidence=confidence,
        data_source=data_sources,
        cache_decision=decision,
        evidence=evidence,
        conflicts=conflicts,
        candidates=ranked,
    )


def _select_primary_group(candidates: list[ProductCandidate]) -> tuple[list[ProductCandidate], list[dict[str, Any]]]:
    primary = candidates[0]
    group = [primary]
    conflicts = []
    for candidate in candidates[1:]:
        similarity = _identity_similarity(primary, candidate)
        if similarity >= 0.38 or _brand_matches(primary, candidate):
            group.append(candidate)
        else:
            conflicts.append(
                {
                    "source": candidate.source,
                    "productBrand": candidate.product_brand,
                    "productName": candidate.product_name,
                    "reason": "identity_conflict",
                    "comparedTo": primary.source,
                    "similarity": round(similarity, 3),
                }
            )
    return group, conflicts


def _merge_group(group: list[ProductCandidate]) -> ProductCandidate:
    primary = group[0]
    authoritative = [candidate for candidate in group if candidate.source != "web_search"] or group
    return ProductCandidate(
        source=primary.source,
        barcode=primary.barcode,
        product_brand=_best_text([candidate.product_brand for candidate in authoritative]) or primary.product_brand,
        product_name=_best_text([candidate.product_name for candidate in authoritative]) or primary.product_name,
        product_url=_first([candidate.product_url for candidate in authoritative]) or _first([candidate.product_url for candidate in group]),
        product_desc=_best_text([candidate.product_desc for candidate in authoritative]) or primary.product_desc,
        product_category=_best_text([candidate.product_category for candidate in authoritative]) or primary.product_category,
        product_image_original=_first([candidate.product_image_original for candidate in authoritative])
        or _first([candidate.product_image_original for candidate in group]),
        container_type=_first([candidate.container_type for candidate in authoritative]),
        container_confidence=_first([candidate.container_confidence for candidate in authoritative]),
        source_confidence=primary.source_confidence,
        evidence=[],
        raw=_merge_raw(group),
    )


def _best_text(values: list[str | None]) -> str | None:
    cleaned = [value for value in values if value]
    if not cleaned:
        return None
    counts = Counter(value.lower() for value in cleaned)
    most_common, count = counts.most_common(1)[0]
    if count > 1:
        for value in cleaned:
            if value.lower() == most_common:
                return value
    return max(cleaned, key=len)


def _first(values):
    return next((value for value in values if value), None)


def _merge_raw(group: list[ProductCandidate]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for candidate in group:
        merged.update(candidate.raw)
    return merged


def _identity_similarity(a: ProductCandidate, b: ProductCandidate) -> float:
    left = a.identity()
    right = b.identity()
    if not left or not right:
        return 0.0
    text_score = SequenceMatcher(None, left, right).ratio()
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    token_score = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return max(text_score, token_score)


def _brand_matches(a: ProductCandidate, b: ProductCandidate) -> bool:
    if not a.product_brand or not b.product_brand:
        return False
    return a.product_brand.strip().lower() == b.product_brand.strip().lower()
