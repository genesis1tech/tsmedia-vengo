"""External source adapters for dry-run product lookup.

These adapters intentionally do not write to AWS resources.
"""

from __future__ import annotations

import os
from typing import Any

import requests

from .barcode import barcode_variants, normalize_barcode
from .models import ProductCandidate


class ProviderError(RuntimeError):
    pass


class ProductLookupProviders:
    def __init__(self, timeout: float = 10.0, session: requests.Session | None = None):
        self.timeout = timeout
        self.session = session or requests.Session()

    def openfoodfacts(self, barcode: str) -> ProductCandidate | None:
        normalized = normalize_barcode(barcode)
        lookup_code = normalized.get("ean13") or normalized["digits"]
        url = f"https://world.openfoodfacts.org/api/v2/product/{lookup_code}.json"
        data = self._get_json(url)
        if data.get("status") == 0:
            return None

        product = data.get("product") or {}
        code = product.get("code") or data.get("code")
        name = product.get("product_name") or product.get("product_name_en")
        brand = product.get("brands")
        if not (name or brand):
            return None

        product_url = product.get("url") or f"https://world.openfoodfacts.org/product/{lookup_code}"
        image = (
            product.get("image_front_url")
            or product.get("image_url")
            or product.get("image_front_small_url")
        )
        evidence = []
        if _barcode_matches(code, barcode):
            evidence.append(f"OpenFoodFacts exact barcode match: {code}")
        if brand:
            evidence.append(f"OpenFoodFacts brand: {brand}")
        if name:
            evidence.append(f"OpenFoodFacts name: {name}")

        return ProductCandidate(
            source="openfoodfacts",
            barcode=code,
            product_brand=_clean(brand),
            product_name=_clean(name),
            product_url=product_url,
            product_desc=_clean(product.get("generic_name") or product.get("abbreviated_product_name")),
            product_category=_clean(product.get("categories")),
            product_image_original=image,
            source_confidence=0.8 if _barcode_matches(code, barcode) else 0.55,
            evidence=evidence,
            raw=product,
        )

    def upcitemdb(self, barcode: str) -> ProductCandidate | None:
        url = "https://api.upcitemdb.com/prod/trial/lookup"
        data = self._get_json(url, params={"upc": normalize_barcode(barcode)["upc12"] or barcode})
        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        name = item.get("title")
        brand = item.get("brand")
        if not (name or brand):
            return None

        code = item.get("upc") or item.get("ean")
        images = item.get("images") or []
        product_url = None
        if item.get("offers"):
            product_url = (item["offers"][0] or {}).get("link")
        product_url = product_url or f"https://www.upcitemdb.com/upc/{normalize_barcode(barcode)['upc12'] or barcode}"

        evidence = []
        if _barcode_matches(code, barcode) or _barcode_matches(item.get("ean"), barcode):
            evidence.append(f"UPCItemDB exact barcode match: {code or item.get('ean')}")
        if brand:
            evidence.append(f"UPCItemDB brand: {brand}")
        if name:
            evidence.append(f"UPCItemDB name: {name}")

        return ProductCandidate(
            source="upcitemdb",
            barcode=code,
            product_brand=_clean(brand),
            product_name=_clean(name),
            product_url=product_url,
            product_desc=_clean(item.get("description")),
            product_category=_clean(item.get("category")),
            product_image_original=images[0] if images else None,
            source_confidence=0.62 if _barcode_matches(code, barcode) else 0.45,
            evidence=evidence,
            raw=item,
        )

    def usda(self, barcode: str) -> ProductCandidate | None:
        api_key = os.environ.get("USDA_API_KEY", "DEMO_KEY")
        url = "https://api.nal.usda.gov/fdc/v1/foods/search"
        data = self._get_json(
            url,
            params={
                "api_key": api_key,
                "query": normalize_barcode(barcode)["upc12"] or barcode,
                "dataType": "Branded",
                "pageSize": 5,
            },
        )
        foods = data.get("foods") or []
        best = _best_usda_food(foods, barcode)
        if not best:
            return None

        name = best.get("description")
        brand = best.get("brandName") or best.get("brandOwner")
        if not (name or brand):
            return None

        evidence = []
        if _barcode_matches(best.get("gtinUpc"), barcode):
            evidence.append(f"USDA exact GTIN/UPC match: {best.get('gtinUpc')}")
        if brand:
            evidence.append(f"USDA brand: {brand}")
        if name:
            evidence.append(f"USDA description: {name}")
        if best.get("householdServingFullText"):
            evidence.append(f"USDA serving: {best.get('householdServingFullText')}")
        if best.get("packageWeight"):
            evidence.append(f"USDA package: {best.get('packageWeight')}")

        return ProductCandidate(
            source="usda",
            barcode=best.get("gtinUpc"),
            product_brand=_clean(brand),
            product_name=_clean(name),
            product_url=f"https://fdc.nal.usda.gov/fdc-app.html#/food-details/{best.get('fdcId')}/nutrients"
            if best.get("fdcId")
            else None,
            product_desc=_clean(best.get("description")),
            product_category=_clean(best.get("foodCategory")),
            product_image_original=None,
            source_confidence=0.88 if _barcode_matches(best.get("gtinUpc"), barcode) else 0.5,
            evidence=evidence,
            raw=best,
        )

    def tavily(self, barcode: str, context: list[ProductCandidate] | None = None) -> list[ProductCandidate]:
        api_key = os.environ.get("TAVILY_API_KEY")
        if not api_key:
            return []

        normalized = normalize_barcode(barcode)
        query = _build_tavily_query(barcode, context or [])
        accepted_terms = _accepted_web_terms(barcode, context or [])
        response = self.session.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": False,
                "include_images": True,
                "include_raw_content": False,
            },
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            raise ProviderError(f"Tavily returned HTTP {response.status_code}: {response.text[:200]}")
        data = response.json()
        images = data.get("images") or []
        candidates = []
        for result in data.get("results") or []:
            text = " ".join(
                str(result.get(key) or "")
                for key in ("title", "content", "raw_content", "url")
            )
            text_lower = text.lower()
            has_barcode = any(variant in text for variant in barcode_variants(barcode))
            has_context = any(term in text_lower for term in accepted_terms)
            if not has_barcode and not has_context:
                continue
            title = _clean(result.get("title"))
            content = _clean(result.get("content"))
            evidence = []
            confidence = 0.58
            if has_barcode:
                evidence.append(f"Tavily result includes exact barcode: {result.get('url')}")
            if has_context:
                evidence.append(f"Tavily result matches structured product context: {result.get('url')}")
                confidence = max(confidence, 0.5)
            candidates.append(
                ProductCandidate(
                    source="web_search",
                    barcode=normalized.get("upc12") or normalized["digits"],
                    product_name=title,
                    product_url=result.get("url"),
                    product_desc=content,
                    product_image_original=images[0] if images else None,
                    source_confidence=confidence,
                    evidence=evidence,
                    raw=result,
                )
            )
        return candidates

    def _get_json(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = self.session.get(
            url,
            params=params,
            timeout=self.timeout,
            headers={"User-Agent": "tsv6-product-lookup-cli/1.0"},
        )
        if response.status_code >= 500:
            raise ProviderError(f"{url} returned HTTP {response.status_code}")
        if response.status_code in (400, 404):
            return {}
        if response.status_code >= 400:
            raise ProviderError(f"{url} returned HTTP {response.status_code}")
        return response.json()


def _best_usda_food(foods: list[dict[str, Any]], barcode: str) -> dict[str, Any] | None:
    for food in foods:
        if _barcode_matches(food.get("gtinUpc"), barcode):
            return food
    return foods[0] if foods else None


def _barcode_matches(candidate: str | None, barcode: str) -> bool:
    if not candidate:
        return False
    candidate_digits = "".join(ch for ch in str(candidate) if ch.isdigit())
    return candidate_digits in barcode_variants(barcode) or candidate_digits.lstrip("0") in barcode_variants(barcode)


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _build_tavily_query(barcode: str, context: list[ProductCandidate]) -> str:
    normalized = normalize_barcode(barcode)
    barcode_query = normalized["upc12"] or normalized["digits"]
    best = next((candidate for candidate in context if candidate.product_name or candidate.product_brand), None)
    if not best:
        return f'"{barcode_query}" product UPC GTIN brand beverage container'

    parts = [f'"{barcode_query}"']
    if best.product_brand:
        parts.append(f'"{best.product_brand}"')
    if best.product_name:
        parts.append(f'"{best.product_name}"')
    parts.append("product beverage container")
    return " ".join(parts)


def _accepted_web_terms(barcode: str, context: list[ProductCandidate]) -> set[str]:
    terms = {variant.lower() for variant in barcode_variants(barcode)}
    for candidate in context:
        for value in (candidate.product_brand, candidate.product_name):
            if not value:
                continue
            cleaned = value.lower()
            if len(cleaned) >= 5:
                terms.add(cleaned)
            for token in cleaned.replace("-", " ").replace(",", " ").split():
                if len(token) >= 5:
                    terms.add(token)
    return terms
