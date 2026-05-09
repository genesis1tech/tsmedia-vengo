"""Models used by the dry-run barcode lookup resolver."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProductCandidate:
    source: str
    barcode: str | None = None
    product_brand: str | None = None
    product_name: str | None = None
    product_url: str | None = None
    product_desc: str | None = None
    product_category: str | None = None
    product_image_original: str | None = None
    container_type: str | None = None
    container_confidence: float | None = None
    source_confidence: float = 0.0
    evidence: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def identity(self) -> str:
        parts = [self.product_brand or "", self.product_name or ""]
        return " ".join(parts).strip().lower()

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "barcode": self.barcode,
            "productBrand": self.product_brand,
            "productName": self.product_name,
            "productUrl": self.product_url,
            "productDesc": self.product_desc,
            "productCategory": self.product_category,
            "productImageOriginal": self.product_image_original,
            "containerType": self.container_type,
            "containerConfidence": self.container_confidence,
            "sourceConfidence": round(self.source_confidence, 3),
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class LookupResult:
    barcode: str
    ean13: str | None
    product_brand: str | None
    product_name: str | None
    product_url: str | None
    product_desc: str | None
    product_category: str | None
    product_image_original: str | None
    container_type: str
    container_confidence: float
    lookup_confidence: float
    data_source: str
    cache_decision: str
    evidence: list[str]
    conflicts: list[dict[str, Any]]
    candidates: list[ProductCandidate]

    def to_dict(self) -> dict[str, Any]:
        return {
            "barcode": self.barcode,
            "ean13": self.ean13,
            "productBrand": self.product_brand,
            "productName": self.product_name,
            "productUrl": self.product_url,
            "productDesc": self.product_desc,
            "productCategory": self.product_category,
            "productImageOriginal": self.product_image_original,
            "containerType": self.container_type,
            "containerConfidence": round(self.container_confidence, 3),
            "lookupConfidence": round(self.lookup_confidence, 3),
            "dataSource": self.data_source,
            "cacheDecision": self.cache_decision,
            "evidence": self.evidence,
            "conflicts": self.conflicts,
            "candidates": [candidate.to_public_dict() for candidate in self.candidates],
        }
