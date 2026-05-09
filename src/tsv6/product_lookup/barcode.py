"""Barcode normalization utilities."""

from __future__ import annotations


def normalize_barcode(raw: str) -> dict[str, str | None]:
    """Return numeric UPC/EAN variants for a scanned barcode string."""
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    upc12: str | None = None
    ean13: str | None = None

    if len(digits) == 12:
        upc12 = digits
        ean13 = f"0{digits}"
    elif len(digits) == 13 and digits.startswith("0"):
        ean13 = digits
        upc12 = digits[1:]
    elif len(digits) == 13:
        ean13 = digits
    elif digits:
        upc12 = digits if len(digits) <= 12 else None
        ean13 = digits if len(digits) == 13 else None

    return {
        "input": raw,
        "digits": digits,
        "upc12": upc12,
        "ean13": ean13,
    }


def barcode_variants(raw: str) -> set[str]:
    normalized = normalize_barcode(raw)
    return {
        value
        for value in (
            normalized.get("digits"),
            normalized.get("upc12"),
            normalized.get("ean13"),
            (normalized.get("digits") or "").lstrip("0"),
        )
        if value
    }
