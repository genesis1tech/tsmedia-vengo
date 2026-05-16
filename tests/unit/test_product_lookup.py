import json
import subprocess
import sys

import pytest

from tsv6.product_lookup.barcode import normalize_barcode
from tsv6.product_lookup.models import ProductCandidate
from tsv6.product_lookup.providers import ProductLookupProviders
from tsv6.product_lookup.scoring import score_candidates


def test_normalize_dashed_upc_to_upc_and_ean():
    assert normalize_barcode("078-742-106274") == {
        "input": "078-742-106274",
        "digits": "078742106274",
        "upc12": "078742106274",
        "ean13": "0078742106274",
    }


def test_clear_american_conflict_scores_cacheable():
    result = score_candidates(
        "078742106274",
        [
            ProductCandidate(
                source="openfoodfacts",
                barcode="0078742106274",
                product_brand="Clear American",
                product_name="Cherry Limeade",
                product_url="https://world.openfoodfacts.org/product/0078742106274",
                product_image_original="https://images.openfoodfacts.org/images/products/007/874/210/6274/front_en.20.400.jpg",
                source_confidence=0.8,
                evidence=["OpenFoodFacts exact barcode match: 0078742106274"],
            ),
            ProductCandidate(
                source="upcitemdb",
                barcode="078742106274",
                product_brand="Member's Mark",
                product_name="180 Count LED Blue Christmas Holiday Lights",
                product_category="Home & Garden",
                source_confidence=0.62,
                evidence=["UPCItemDB exact barcode match: 078742106274"],
            ),
            ProductCandidate(
                source="usda",
                barcode="078742106274",
                product_brand="CLEAR AMERICAN",
                product_name="CHERRY LIMEADE FLAVORED SPARKLING WATER BEVERAGE, CHERRY LIMEADE",
                product_desc="CHERRY LIMEADE FLAVORED SPARKLING WATER BEVERAGE, CHERRY LIMEADE",
                product_category="Water",
                source_confidence=0.88,
                evidence=["USDA exact GTIN/UPC match: 078742106274", "USDA serving: 1 can"],
                raw={"householdServingFullText": "1 can", "packageWeight": "12 fl oz/355 mL"},
            ),
        ],
    )

    data = result.to_dict()
    assert data["cacheDecision"] == "cacheable"
    assert data["productBrand"] in {"CLEAR AMERICAN", "Clear American"}
    assert "CHERRY LIMEADE" in data["productName"].upper()
    assert data["containerType"] == "can"
    assert data["containerConfidence"] == 0.9
    assert data["lookupConfidence"] >= 0.8
    assert data["conflicts"][0]["source"] == "upcitemdb"


def test_web_search_support_does_not_override_structured_fields():
    result = score_candidates(
        "070847898146",
        [
            ProductCandidate(
                source="openfoodfacts",
                barcode="0070847898146",
                product_brand="Monster",
                product_name="Ultra Vice Guava",
                product_url="https://world.openfoodfacts.org/product/0070847898146",
                product_image_original="https://images.openfoodfacts.org/images/products/007/084/789/8146/front_en.54.400.jpg",
                product_category="energy-drinks",
                source_confidence=0.8,
                evidence=["OpenFoodFacts exact barcode match: 0070847898146"],
            ),
            ProductCandidate(
                source="web_search",
                barcode="070847898146",
                product_name="Monster Energy Ultra Vice Guava Energy Drink - 16 fl oz Can : Target",
                product_url="https://www.target.com/p/monster-energy-ultra-vice-guava-energy-drink-16-fl-oz-can/-/A-93326726",
                product_desc="Long noisy retail navigation content " * 20,
                source_confidence=0.58,
                evidence=["Tavily result matches structured product context"],
            ),
        ],
    )

    data = result.to_dict()
    assert data["cacheDecision"] == "cacheable"
    assert data["productName"] == "Ultra Vice Guava"
    assert data["productUrl"] == "https://world.openfoodfacts.org/product/0070847898146"
    assert data["productImageOriginal"].startswith("https://images.openfoodfacts.org/")
    assert data["dataSource"] == "openfoodfacts+web_search"


def test_reign_brand_fallback_provides_container_fields():
    result = score_candidates(
        "815154026277",
        [
            ProductCandidate(
                source="openfoodfacts",
                barcode="0815154026277",
                product_brand="REIGN",
                product_name="Storm",
                product_url="https://world.openfoodfacts.org/product/0815154026277",
                product_image_original="https://images.openfoodfacts.org/images/products/081/515/402/6277/front_en.8.400.jpg",
                source_confidence=0.8,
                evidence=["OpenFoodFacts exact barcode match: 0815154026277"],
            )
        ],
    )

    data = result.to_dict()
    assert data["cacheDecision"] == "cacheable"
    assert data["containerType"] == "can"
    assert data["containerConfidence"] == 0.55


def test_exact_barcode_web_result_enriches_generic_reign_name():
    result = score_candidates(
        "815154026277",
        [
            ProductCandidate(
                source="openfoodfacts",
                barcode="0815154026277",
                product_brand="REIGN",
                product_name="Storm",
                product_url="https://world.openfoodfacts.org/product/0815154026277",
                product_image_original="https://images.openfoodfacts.org/images/products/081/515/402/6277/front_en.8.400.jpg",
                source_confidence=0.8,
                evidence=["OpenFoodFacts exact barcode match: 0815154026277"],
            ),
            ProductCandidate(
                source="web_search",
                barcode="815154026277",
                product_name="815154026277 Energy Drink, Liquid, Mango, 12 fl-oz Can",
                product_url="https://www.mccoys.com/shop/p/35015036/reign-storm-815154026277-energy-drink-liquid-mango-12-fl-oz-can",
                product_desc="Energy Drink, Liquid, Mango, 12 fl-oz Can",
                source_confidence=0.58,
                evidence=["Tavily result includes exact barcode: https://www.mccoys.com/shop/p/35015036/reign-storm-815154026277-energy-drink-liquid-mango-12-fl-oz-can"],
                raw={
                    "title": "815154026277 Energy Drink, Liquid, Mango, 12 fl-oz Can",
                    "url": "https://www.mccoys.com/shop/p/35015036/reign-storm-815154026277-energy-drink-liquid-mango-12-fl-oz-can",
                },
            ),
        ],
    )

    data = result.to_dict()
    assert data["cacheDecision"] == "cacheable"
    assert data["productName"] == "REIGN Storm Mango"
    assert data["dataSource"] == "openfoodfacts+web_search"
    assert data["containerType"] == "can"
    assert data["containerConfidence"] == 0.9
    assert data["conflicts"] == []


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payload):
        self.payload = payload
        self.last_url = None
        self.last_params = None
        self.last_json = None

    def get(self, url, params=None, timeout=None, headers=None):
        self.last_url = url
        self.last_params = params
        return FakeResponse(self.payload)

    def post(self, url, headers=None, json=None, timeout=None):
        self.last_url = url
        self.last_json = json
        return FakeResponse(self.payload)


def test_openfoodfacts_normalizer_extracts_expected_fields():
    providers = ProductLookupProviders(
        session=FakeSession(
            {
                "status": 1,
                "code": "0078742106274",
                "product": {
                    "code": "0078742106274",
                    "product_name": "Cherry Limeade",
                    "brands": "Clear American",
                    "image_front_url": "https://example.test/front.jpg",
                },
            }
        )
    )

    candidate = providers.openfoodfacts("078742106274")

    assert candidate.product_brand == "Clear American"
    assert candidate.product_name == "Cherry Limeade"
    assert candidate.product_image_original == "https://example.test/front.jpg"
    assert candidate.source_confidence == 0.8


def test_usda_normalizer_extracts_container_evidence():
    providers = ProductLookupProviders(
        session=FakeSession(
            {
                "foods": [
                    {
                        "fdcId": 2504874,
                        "description": "CHERRY LIMEADE FLAVORED SPARKLING WATER BEVERAGE",
                        "gtinUpc": "078742106274",
                        "brandName": "CLEAR AMERICAN",
                        "foodCategory": "Water",
                        "packageWeight": "12 fl oz/355 mL",
                        "householdServingFullText": "1 can",
                    }
                ]
            }
        )
    )

    candidate = providers.usda("078742106274")

    assert candidate.product_brand == "CLEAR AMERICAN"
    assert candidate.product_category == "Water"
    assert candidate.source_confidence == 0.88
    assert "USDA serving: 1 can" in candidate.evidence


def test_tavily_uses_structured_context_for_enrichment(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    session = FakeSession(
        {
            "images": ["https://example.test/monster.jpg"],
            "results": [
                {
                    "title": "Monster Ultra Vice Guava Energy Drink",
                    "url": "https://example.test/monster-vice-guava",
                    "content": "Monster Ultra Vice Guava is a zero sugar energy drink in a 16 fl oz can.",
                },
                {
                    "title": "UPC barcode standards",
                    "url": "https://example.test/upc",
                    "content": "General barcode documentation.",
                },
            ],
        }
    )
    providers = ProductLookupProviders(session=session)

    candidates = providers.tavily(
        "070847898146",
        context=[
            ProductCandidate(
                source="openfoodfacts",
                product_brand="Monster",
                product_name="Ultra Vice Guava",
            )
        ],
    )

    assert "Ultra Vice Guava" in session.last_json["query"]
    assert len(candidates) == 1
    assert candidates[0].product_url == "https://example.test/monster-vice-guava"
    assert "matches structured product context" in candidates[0].evidence[0]


def test_cli_no_web_outputs_json_without_aws_writes():
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/product_lookup_cli.py",
            "078-742-106274",
            "--json",
            "--no-web",
            "--timeout",
            "15",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode in (0, 2)
    data = json.loads(completed.stdout)
    assert data["barcode"] == "078742106274"
    assert "cacheDecision" in data
    assert "candidates" in data


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (ProductCandidate(source="test", product_name="Sparkling Water", raw={"householdServingFullText": "1 can"}), "can"),
        (ProductCandidate(source="test", product_desc="Glass bottle lemonade"), "glass_bottle"),
        (ProductCandidate(source="test", product_desc="PET bottle water"), "plastic_bottle"),
        (ProductCandidate(source="test", product_brand="REIGN", product_name="Storm"), "can"),
    ],
)
def test_container_inference(candidate, expected):
    from tsv6.product_lookup.container import infer_container

    assert infer_container(candidate)[0] == expected
