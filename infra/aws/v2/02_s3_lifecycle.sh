#!/usr/bin/env bash
# Apply lifecycle rules to topper-stopper-bucket for V2 prefixes.
# Adds/replaces only the V2-tagged rules; leaves existing V1 rules untouched.
set -euo pipefail

BUCKET=topper-stopper-bucket
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT

# Pull current lifecycle configuration if any, strip any existing tsv6-v2-* rules,
# then append the new ones.
EXISTING=$(aws s3api get-bucket-lifecycle-configuration --bucket "$BUCKET" 2>/dev/null \
  | jq '.Rules // [] | map(select(.ID | startswith("tsv6-v2-") | not))' || echo '[]')

cat > "$TMP" <<JSON
{
  "Rules": $(echo "$EXISTING" | jq -c '. + [
    {
      "ID": "tsv6-v2-scans-tiering",
      "Status": "Enabled",
      "Filter": {"Prefix": "scans-v2/"},
      "Transitions": [
        {"Days":  30, "StorageClass": "STANDARD_IA"},
        {"Days":  90, "StorageClass": "GLACIER_IR"}
      ],
      "Expiration": {"Days": 730}
    },
    {
      "ID": "tsv6-v2-scans-errors-expire",
      "Status": "Enabled",
      "Filter": {"Prefix": "scans-v2-errors/"},
      "Expiration": {"Days": 30}
    }
  ]')
}
JSON

aws s3api put-bucket-lifecycle-configuration --bucket "$BUCKET" \
  --lifecycle-configuration "file://$TMP"

# Create marker objects so the prefixes appear in the console immediately.
# AWS CLI v2 rejects /dev/null for --body, so use an empty regular file.
EMPTY=$(mktemp)
trap 'rm -f "$TMP" "$EMPTY"' EXIT
aws s3api put-object --bucket "$BUCKET" --key "scans-v2/.keep"             --body "$EMPTY" >/dev/null
aws s3api put-object --bucket "$BUCKET" --key "scans-v2-errors/.keep"      --body "$EMPTY" >/dev/null
aws s3api put-object --bucket "$BUCKET" --key "product-images-webp/.keep"  --body "$EMPTY" >/dev/null
aws s3api put-object --bucket "$BUCKET" --key "athena-results/.keep"       --body "$EMPTY" >/dev/null

echo "Done."
