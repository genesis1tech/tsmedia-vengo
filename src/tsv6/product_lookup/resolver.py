"""Dry-run product lookup resolver."""

from __future__ import annotations

from .models import LookupResult, ProductCandidate
from .providers import ProductLookupProviders
from .scoring import score_candidates


class ProductLookupResolver:
    def __init__(self, providers: ProductLookupProviders | None = None):
        self.providers = providers or ProductLookupProviders()

    def resolve(self, barcode: str, *, use_web: bool = True) -> LookupResult:
        candidates: list[ProductCandidate] = []
        errors: list[ProductCandidate] = []

        for source_name in ("openfoodfacts", "upcitemdb", "usda"):
            try:
                candidate = getattr(self.providers, source_name)(barcode)
            except Exception as exc:  # noqa: BLE001 - dry-run should report all provider failures.
                errors.append(
                    ProductCandidate(
                        source=source_name,
                        source_confidence=0.0,
                        evidence=[f"{source_name} error: {exc}"],
                    )
                )
                continue
            if candidate:
                candidates.append(candidate)

        if use_web:
            try:
                candidates.extend(self.providers.tavily(barcode, context=candidates))
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    ProductCandidate(
                        source="web_search",
                        source_confidence=0.0,
                        evidence=[f"web_search error: {exc}"],
                    )
                )

        result = score_candidates(barcode, candidates)
        if errors:
            result.evidence.extend(item.evidence[0] for item in errors if item.evidence)
        return result
