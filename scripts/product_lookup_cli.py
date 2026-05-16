#!/usr/bin/env python3
"""Dry-run barcode product lookup CLI.

This command never writes to DynamoDB, S3, Firehose, or the negative cache.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from tsv6.product_lookup import ProductLookupResolver  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dry-run product lookup for a barcode.")
    parser.add_argument("barcode", help="Barcode to look up, dashes are allowed")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    parser.add_argument("--no-web", action="store_true", help="Skip Tavily/web-search enrichment")
    parser.add_argument("--timeout", type=float, default=10.0, help="HTTP timeout in seconds")
    args = parser.parse_args(argv)

    resolver = ProductLookupResolver()
    resolver.providers.timeout = args.timeout
    result = resolver.resolve(args.barcode, use_web=not args.no_web)
    data = result.to_dict()

    if args.json:
        print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _print_human(data)
    return 0 if data["cacheDecision"] in {"cacheable", "review"} else 2


def _print_human(data: dict) -> None:
    print(f"Barcode: {data['barcode']}")
    if data.get("ean13"):
        print(f"EAN-13: {data['ean13']}")
    print(f"Decision: {data['cacheDecision']} ({data['lookupConfidence']})")
    print(f"Source: {data['dataSource']}")
    print(f"Brand: {data.get('productBrand') or '-'}")
    print(f"Name: {data.get('productName') or '-'}")
    print(f"URL: {data.get('productUrl') or '-'}")
    print(f"Description: {data.get('productDesc') or '-'}")
    print(f"Category: {data.get('productCategory') or '-'}")
    print(f"Image: {data.get('productImageOriginal') or '-'}")
    print(f"Container: {data['containerType']} ({data['containerConfidence']})")
    print()
    print("Evidence:")
    for item in data["evidence"] or ["No supporting evidence found."]:
        print(f"- {item}")
    if data["conflicts"]:
        print()
        print("Conflicts:")
        for conflict in data["conflicts"]:
            brand = conflict.get("productBrand") or "-"
            name = conflict.get("productName") or "-"
            print(f"- {conflict['source']}: {brand} / {name} ({conflict['reason']})")


if __name__ == "__main__":
    raise SystemExit(main())
