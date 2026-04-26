#!/usr/bin/env bash
# Create brand_playlists DynamoDB table and seed the *default* row.
# Idempotent: existing table or item is left alone.
set -euo pipefail

TABLE=brand_playlists

if aws dynamodb describe-table --table-name "$TABLE" >/dev/null 2>&1; then
  echo "Table $TABLE already exists, skipping create."
else
  aws dynamodb create-table \
    --table-name "$TABLE" \
    --attribute-definitions AttributeName=brand,AttributeType=S \
    --key-schema AttributeName=brand,KeyType=HASH \
    --billing-mode PAY_PER_REQUEST \
    --tags Key=Project,Value=tsv6 Key=Component,Value=barcode-v2
  aws dynamodb wait table-exists --table-name "$TABLE"
  echo "Created $TABLE."
fi

echo "Seeding *default* row..."
aws dynamodb put-item --table-name "$TABLE" --item '{
  "brand":            {"S": "*default*"},
  "depositPlaylist":  {"S": "tsv6_processing"},
  "productPlaylist":  {"S": "tsv6_product_display"}
}' --condition-expression "attribute_not_exists(brand)" 2>&1 | grep -v ConditionalCheckFailed || true

echo "Done."
