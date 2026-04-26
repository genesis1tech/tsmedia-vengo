# BarcodeRepoLookupV2 + UpdatedBarcodeToGoUPCV2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the V1 IoT-triggered barcode lookup pipeline (DynamoDB Pilot_Scans put + S3 read-modify-write of `all_preprod_scans.json` on every scan) with a V2 pipeline that writes one Firehose record per scan to a partitioned Parquet store, converts product images to WebP for LTE bandwidth savings, and lets the cloud drive PiSignage playlist selection per brand. V1 path stays untouched so existing fleet is unaffected.

**Architecture:** Two new Lambdas (`BarcodeRepoLookupV2`, `UpdatedBarcodeToGoUPCV2`) sit behind a new IoT rule that fires only when `state.reported.flowVersion = 'v2'`. The hot path is one DDB GetItem on `master_products` plus one Firehose PutRecord. The cold path resolves via GoUPC fallbacks then converts the source image to WebP off the openDoor critical path. Brand playlist routing comes from a tiny new `brand_playlists` DDB table. Device side gets a one-line shadow flag and three small `playlist_override` parameter additions to existing PiSignage adapter methods.

**Tech Stack:** AWS — Lambda (python3.12), DynamoDB, Kinesis Firehose, Glue, Athena, S3, IoT Core, IAM. Tooling — `awscli` for AWS work, `uv` for Python on the Pi, pytest with `unittest.mock` for tests. Pillow for WebP conversion (via klayers public Lambda layer).

**Spec:** `docs/superpowers/specs/2026-04-25-barcode-repo-lookup-v2-design.md` (commit `c703b10`).

**Pre-requisites for the executor:**
- Fresh AWS credentials exported as `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` with permissions to create IAM, DynamoDB, Lambda, Firehose, Glue, Athena, S3, IoT resources in account `010526276861` / `us-east-1`. (The credentials in the conversation transcript were leaked and must be rotated before this plan executes.)
- Working directory: `/home/g1tech/tsrpi7/tsrpi5`.
- Python venv: `./.venv/bin/python` (already exists; managed by `uv`).
- Bench device: `TS_EFFC94AA` (already provisioned with certs at `assets/certs/`).
- All `aws` commands run with `--region us-east-1` already in env: `export AWS_DEFAULT_REGION=us-east-1`.

**Conventions for every AWS call below:**
- Resources are namespaced with `tsv6-` (Lambdas, roles, Firehose) or `_v2` suffix (DynamoDB tables, Glue table, Athena view) so they sit beside V1 resources without collision.
- All commands are idempotent or guarded — if a resource exists, the step passes. Re-running the plan must be safe.

---

## File structure

**Create (in `tsrpi5` repo)**

| File | Responsibility |
|---|---|
| `lambdas/barcode_repo_lookup_v2/lambda_function.py` | V2 hot-path Lambda. Master_products lookup → publish openDoor + Firehose row, or negative cache → noMatch + Firehose row, or async invoke `UpdatedBarcodeToGoUPCV2`. |
| `lambdas/barcode_repo_lookup_v2/requirements.txt` | (Empty — V2 hot-path uses only boto3, which is in the Lambda runtime.) |
| `lambdas/barcode_repo_lookup_v2/deploy.sh` | Package and deploy `BarcodeRepoLookupV2`. |
| `lambdas/barcode_repo_lookup_v2/tests/__init__.py` | Empty marker. |
| `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py` | Unit tests for V2 hot-path Lambda. |
| `lambdas/updated_barcode_to_go_upc_v2/lambda_function.py` | V2 cold-path Lambda. UPC fallback chain → publish openDoor with `productImage: null` → background WebP conversion → master_products write. |
| `lambdas/updated_barcode_to_go_upc_v2/requirements.txt` | `requests` (Pillow comes from the layer). |
| `lambdas/updated_barcode_to_go_upc_v2/deploy.sh` | Package and deploy `UpdatedBarcodeToGoUPCV2`. |
| `lambdas/updated_barcode_to_go_upc_v2/tests/__init__.py` | Empty marker. |
| `lambdas/updated_barcode_to_go_upc_v2/tests/test_lambda.py` | Unit tests for V2 cold-path Lambda. |
| `infra/aws/v2/01_brand_playlists.sh` | Create `brand_playlists` DDB table + seed `*default*` row. |
| `infra/aws/v2/02_s3_lifecycle.sh` | Set lifecycle policy on `topper-stopper-bucket` for the `scans-v2/` prefix and create the `product-images-webp/` prefix marker. |
| `infra/aws/v2/03_glue_table.sh` | Create Glue database (if needed) + Glue table `tsv6.scans_v2` with the V2 schema. |
| `infra/aws/v2/04_firehose_stream.sh` | Create Kinesis Firehose `tsv6-scans-v2` with Parquet conversion + dynamic partitioning. |
| `infra/aws/v2/05_athena_workgroup.sh` | Create Athena workgroup `tsv6-analytics` with 1 GB per-query cap + create view `tsv6.v_scans_v2`. |
| `infra/aws/v2/06_iam_role.sh` | Create IAM role `tsv6-lambda-v2-role` with policies for both V2 Lambdas. |
| `infra/aws/v2/07_iot_rule.sh` | Create IoT rule `barcodeRepoLookupV2`; update existing `barcodeRepoLookup` to exclude V2 traffic and drop the dead `sb_BarcodeRepoLookup` action. |
| `infra/aws/v2/glue_table_schema.json` | Glue table input JSON (referenced by `03_glue_table.sh`). |
| `infra/aws/v2/firehose_config.json` | Firehose extended configuration JSON. |
| `infra/aws/v2/iam_trust.json` | Lambda assume-role trust policy. |
| `infra/aws/v2/iam_policy.json` | Inline policy JSON for `tsv6-lambda-v2-role`. |
| `scripts/v2_smoke_test.py` | End-to-end verification on bench device `TS_EFFC94AA`. |

**Modify**

| File | What changes |
|---|---|
| `src/tsv6/core/main.py` | `publish_to_aws_iot` adds `"flowVersion": "v2"` to `state.reported`. |
| `src/tsv6/core/production_main.py` | Same `flowVersion` addition. Thread `noItemPlaylist` from openDoor payload through to `show_no_item_detected`. Thread `noMatchPlaylist` to `show_no_match`. Thread `barcodeNotQrPlaylist` to `show_barcode_not_qr`. Handle `productImage = None` (text-only render). |
| `src/tsv6/display/pisignage_adapter.py` | Add `playlist_override` kwarg to `show_no_match`, `show_no_item_detected`, `show_barcode_not_qr`. |
| `src/tsv6/display/controller.py` | Update `DisplayController` Protocol: same three methods accept `playlist_override`. |
| `src/tsv6/display/tsv6_player/backend.py` | `TSV6NativeBackend` mirrors the new kwarg signatures (kwarg ignored, native backend has no per-call playlist concept). |
| `tests/unit/test_pisignage_adapter.py` | Tests for the new `playlist_override` kwargs on the three methods. |
| `src/tsv6/core/image_manager.py` | `_show_image_overlay` (and any sibling) tolerates `productImage` being `None` / `""` and renders the existing text card without trying to load an image. |

---

## Phase 1 — AWS infrastructure

These tasks build resources in idempotent shell scripts checked into the repo. Each script is safe to re-run.

### Task 1: brand_playlists DDB table

**Files:**
- Create: `infra/aws/v2/01_brand_playlists.sh`

- [ ] **Step 1: Verify the table does not yet exist (run by hand once before writing the script)**

```bash
aws dynamodb describe-table --table-name brand_playlists 2>&1 | head -3
```

Expected: `An error occurred (ResourceNotFoundException)` — confirms the name is free.

- [ ] **Step 2: Write the script**

```bash
cat > infra/aws/v2/01_brand_playlists.sh <<'EOF'
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
EOF
chmod +x infra/aws/v2/01_brand_playlists.sh
```

- [ ] **Step 3: Run the script**

```bash
mkdir -p infra/aws/v2 && bash infra/aws/v2/01_brand_playlists.sh
```

Expected output (first run): `Created brand_playlists.` then `Seeding *default* row...` then `Done.`

- [ ] **Step 4: Verify the row was written**

```bash
aws dynamodb get-item --table-name brand_playlists --key '{"brand":{"S":"*default*"}}'
```

Expected: JSON with `"depositPlaylist": {"S": "tsv6_processing"}` and `"productPlaylist": {"S": "tsv6_product_display"}`.

- [ ] **Step 5: Re-run the script to confirm idempotence**

```bash
bash infra/aws/v2/01_brand_playlists.sh
```

Expected: `Table brand_playlists already exists, skipping create.` then `Done.`

- [ ] **Step 6: Commit**

```bash
git add infra/aws/v2/01_brand_playlists.sh
git commit -m "infra(v2): brand_playlists DDB table + default playlist row"
```

---

### Task 2: S3 prefixes and lifecycle policy

**Files:**
- Create: `infra/aws/v2/02_s3_lifecycle.sh`

- [ ] **Step 1: Write the script**

```bash
cat > infra/aws/v2/02_s3_lifecycle.sh <<'EOF'
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
aws s3api put-object --bucket "$BUCKET" --key "scans-v2/.keep"             --body /dev/null
aws s3api put-object --bucket "$BUCKET" --key "scans-v2-errors/.keep"      --body /dev/null
aws s3api put-object --bucket "$BUCKET" --key "product-images-webp/.keep"  --body /dev/null
aws s3api put-object --bucket "$BUCKET" --key "athena-results/.keep"       --body /dev/null

echo "Done."
EOF
chmod +x infra/aws/v2/02_s3_lifecycle.sh
```

- [ ] **Step 2: Run the script**

```bash
bash infra/aws/v2/02_s3_lifecycle.sh
```

Expected: `Done.` (no error).

- [ ] **Step 3: Verify lifecycle rules**

```bash
aws s3api get-bucket-lifecycle-configuration --bucket topper-stopper-bucket \
  | jq '.Rules[] | select(.ID | startswith("tsv6-v2-"))'
```

Expected: two rules (`tsv6-v2-scans-tiering` and `tsv6-v2-scans-errors-expire`).

- [ ] **Step 4: Verify prefix markers**

```bash
aws s3 ls s3://topper-stopper-bucket/scans-v2/
aws s3 ls s3://topper-stopper-bucket/product-images-webp/
```

Expected: each lists `.keep` (0 B).

- [ ] **Step 5: Commit**

```bash
git add infra/aws/v2/02_s3_lifecycle.sh
git commit -m "infra(v2): S3 lifecycle for scans-v2 + WebP and athena-results prefixes"
```

---

### Task 3: Glue table for scans_v2

**Files:**
- Create: `infra/aws/v2/glue_table_schema.json`
- Create: `infra/aws/v2/03_glue_table.sh`

- [ ] **Step 1: Write the schema**

```bash
cat > infra/aws/v2/glue_table_schema.json <<'EOF'
{
  "Name": "scans_v2",
  "Description": "TSV6 V2 barcode scan events (Firehose-delivered Parquet)",
  "TableType": "EXTERNAL_TABLE",
  "Parameters": {
    "classification": "parquet",
    "projection.enabled": "true",
    "projection.yyyy.type": "integer",
    "projection.yyyy.range": "2026,2035",
    "projection.mm.type":   "integer",
    "projection.mm.range":  "1,12",
    "projection.mm.digits": "2",
    "projection.dd.type":   "integer",
    "projection.dd.range":  "1,31",
    "projection.dd.digits": "2",
    "projection.hh.type":   "integer",
    "projection.hh.range":  "0,23",
    "projection.hh.digits": "2",
    "storage.location.template": "s3://topper-stopper-bucket/scans-v2/yyyy=${yyyy}/mm=${mm}/dd=${dd}/hh=${hh}/"
  },
  "PartitionKeys": [
    {"Name": "yyyy", "Type": "string"},
    {"Name": "mm",   "Type": "string"},
    {"Name": "dd",   "Type": "string"},
    {"Name": "hh",   "Type": "string"}
  ],
  "StorageDescriptor": {
    "Columns": [
      {"Name": "transactionid",         "Type": "string"},
      {"Name": "thingname",             "Type": "string"},
      {"Name": "barcode",               "Type": "string"},
      {"Name": "scantimestamp",         "Type": "timestamp"},
      {"Name": "eventtype",             "Type": "string"},
      {"Name": "returnaction",          "Type": "string"},
      {"Name": "productname",           "Type": "string"},
      {"Name": "productbrand",          "Type": "string"},
      {"Name": "productcategory",       "Type": "string"},
      {"Name": "productdesc",           "Type": "string"},
      {"Name": "productimage",          "Type": "string"},
      {"Name": "productimageoriginal",  "Type": "string"},
      {"Name": "containertype",         "Type": "string"},
      {"Name": "containerconfidence",   "Type": "double"},
      {"Name": "datasource",            "Type": "string"},
      {"Name": "lookuplatencyms",       "Type": "int"},
      {"Name": "qrurl",                 "Type": "string"},
      {"Name": "depositplaylist",       "Type": "string"},
      {"Name": "productplaylist",       "Type": "string"},
      {"Name": "noitemplaylist",        "Type": "string"},
      {"Name": "nomatchplaylist",       "Type": "string"},
      {"Name": "barcodenotqrplaylist",  "Type": "string"},
      {"Name": "reason",                "Type": "string"},
      {"Name": "flowversion",           "Type": "string"},
      {"Name": "lambdaname",            "Type": "string"},
      {"Name": "lambdaversion",         "Type": "string"}
    ],
    "Location":      "s3://topper-stopper-bucket/scans-v2/",
    "InputFormat":   "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat",
    "OutputFormat":  "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat",
    "SerdeInfo": {
      "SerializationLibrary": "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe",
      "Parameters": {"serialization.format": "1"}
    },
    "Compressed": true
  }
}
EOF
```

- [ ] **Step 2: Write the script**

```bash
cat > infra/aws/v2/03_glue_table.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
DB=tsv6
TABLE=scans_v2

if ! aws glue get-database --name "$DB" >/dev/null 2>&1; then
  aws glue create-database --database-input "Name=$DB,Description=TSV6 analytics tables"
  echo "Created Glue database $DB."
fi

if aws glue get-table --database-name "$DB" --name "$TABLE" >/dev/null 2>&1; then
  echo "Table $DB.$TABLE exists, updating..."
  aws glue update-table --database-name "$DB" --table-input "file://infra/aws/v2/glue_table_schema.json"
else
  echo "Creating $DB.$TABLE..."
  aws glue create-table  --database-name "$DB" --table-input "file://infra/aws/v2/glue_table_schema.json"
fi
echo "Done."
EOF
chmod +x infra/aws/v2/03_glue_table.sh
```

- [ ] **Step 3: Run it**

```bash
bash infra/aws/v2/03_glue_table.sh
```

Expected: `Created Glue database tsv6.` (first run only) then `Creating tsv6.scans_v2...` then `Done.`

- [ ] **Step 4: Verify**

```bash
aws glue get-table --database-name tsv6 --name scans_v2 \
  --query 'Table.StorageDescriptor.Columns | length(@)'
```

Expected: `26` (the number of non-partition columns above).

- [ ] **Step 5: Commit**

```bash
git add infra/aws/v2/03_glue_table.sh infra/aws/v2/glue_table_schema.json
git commit -m "infra(v2): Glue database + scans_v2 table with partition projection"
```

---

### Task 4: Firehose stream

**Files:**
- Create: `infra/aws/v2/firehose_config.json`
- Create: `infra/aws/v2/04_firehose_stream.sh`

- [ ] **Step 1: Create the Firehose-to-S3 IAM role**

The Firehose stream needs its own role distinct from the Lambda role. Build it inline in the script — once.

- [ ] **Step 2: Write the config JSON**

```bash
cat > infra/aws/v2/firehose_config.json <<'EOF'
{
  "RoleARN": "__FIREHOSE_ROLE_ARN__",
  "BucketARN": "arn:aws:s3:::topper-stopper-bucket",
  "Prefix": "scans-v2/yyyy=!{partitionKeyFromQuery:year}/mm=!{partitionKeyFromQuery:month}/dd=!{partitionKeyFromQuery:day}/hh=!{partitionKeyFromQuery:hour}/",
  "ErrorOutputPrefix": "scans-v2-errors/!{firehose:error-output-type}/yyyy=!{timestamp:yyyy}/mm=!{timestamp:MM}/dd=!{timestamp:dd}/",
  "BufferingHints": {"SizeInMBs": 128, "IntervalInSeconds": 300},
  "CompressionFormat": "UNCOMPRESSED",
  "DataFormatConversionConfiguration": {
    "Enabled": true,
    "InputFormatConfiguration":  {"Deserializer": {"OpenXJsonSerDe": {}}},
    "OutputFormatConfiguration": {"Serializer":   {"ParquetSerDe":   {"Compression": "SNAPPY"}}},
    "SchemaConfiguration": {
      "RoleARN":      "__FIREHOSE_ROLE_ARN__",
      "DatabaseName": "tsv6",
      "TableName":    "scans_v2",
      "Region":       "us-east-1",
      "VersionId":    "LATEST"
    }
  },
  "DynamicPartitioningConfiguration": {
    "Enabled": true,
    "RetryOptions": {"DurationInSeconds": 300}
  },
  "ProcessingConfiguration": {
    "Enabled": true,
    "Processors": [
      {
        "Type": "MetadataExtraction",
        "Parameters": [
          {"ParameterName": "MetadataExtractionQuery", "ParameterValue": "{year:.scanTimestamp[0:4],month:.scanTimestamp[5:7],day:.scanTimestamp[8:10],hour:.scanTimestamp[11:13]}"},
          {"ParameterName": "JsonParsingEngine", "ParameterValue": "JQ-1.6"}
        ]
      }
    ]
  }
}
EOF
```

- [ ] **Step 3: Write the script**

```bash
cat > infra/aws/v2/04_firehose_stream.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

ROLE=tsv6-firehose-scans-v2-role
STREAM=tsv6-scans-v2

# 1. Create Firehose role if missing.
if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" --assume-role-policy-document '{
    "Version":"2012-10-17",
    "Statement":[{"Effect":"Allow","Principal":{"Service":"firehose.amazonaws.com"},"Action":"sts:AssumeRole"}]
  }' >/dev/null
  echo "Created role $ROLE."
fi

ROLE_ARN=$(aws iam get-role --role-name "$ROLE" --query 'Role.Arn' --output text)

aws iam put-role-policy --role-name "$ROLE" --policy-name firehose-s3-glue --policy-document '{
  "Version":"2012-10-17",
  "Statement":[
    {"Effect":"Allow","Action":["s3:AbortMultipartUpload","s3:GetBucketLocation","s3:GetObject","s3:ListBucket","s3:ListBucketMultipartUploads","s3:PutObject"],
     "Resource":["arn:aws:s3:::topper-stopper-bucket","arn:aws:s3:::topper-stopper-bucket/scans-v2/*","arn:aws:s3:::topper-stopper-bucket/scans-v2-errors/*"]},
    {"Effect":"Allow","Action":["glue:GetTable","glue:GetTableVersion","glue:GetTableVersions"],
     "Resource":["arn:aws:glue:us-east-1:010526276861:catalog","arn:aws:glue:us-east-1:010526276861:database/tsv6","arn:aws:glue:us-east-1:010526276861:table/tsv6/scans_v2"]},
    {"Effect":"Allow","Action":["logs:PutLogEvents"],"Resource":"*"}
  ]
}'

# Wait for IAM propagation (Firehose creation can fail if the role isn't visible yet).
sleep 10

# 2. Render the config with the resolved role ARN.
TMP=$(mktemp)
trap 'rm -f "$TMP"' EXIT
sed "s|__FIREHOSE_ROLE_ARN__|$ROLE_ARN|g" infra/aws/v2/firehose_config.json > "$TMP"

# 3. Create or update the stream.
if aws firehose describe-delivery-stream --delivery-stream-name "$STREAM" >/dev/null 2>&1; then
  echo "Stream $STREAM exists; skipping create. Edit-in-place not implemented in this script."
else
  aws firehose create-delivery-stream \
    --delivery-stream-name "$STREAM" \
    --delivery-stream-type DirectPut \
    --extended-s3-destination-configuration "file://$TMP"
  echo "Created Firehose stream $STREAM."
fi
echo "Done."
EOF
chmod +x infra/aws/v2/04_firehose_stream.sh
```

- [ ] **Step 4: Run it**

```bash
bash infra/aws/v2/04_firehose_stream.sh
```

Expected: `Created role tsv6-firehose-scans-v2-role.` (first run) then `Created Firehose stream tsv6-scans-v2.` then `Done.`

- [ ] **Step 5: Verify stream is ACTIVE**

```bash
aws firehose describe-delivery-stream --delivery-stream-name tsv6-scans-v2 \
  --query 'DeliveryStreamDescription.DeliveryStreamStatus'
```

Expected: `"ACTIVE"` (may take ~30s; if `"CREATING"`, wait and retry).

- [ ] **Step 6: Commit**

```bash
git add infra/aws/v2/04_firehose_stream.sh infra/aws/v2/firehose_config.json
git commit -m "infra(v2): Kinesis Firehose tsv6-scans-v2 with Parquet conversion"
```

---

### Task 5: Athena workgroup + view

**Files:**
- Create: `infra/aws/v2/05_athena_workgroup.sh`

- [ ] **Step 1: Write the script**

```bash
cat > infra/aws/v2/05_athena_workgroup.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
WG=tsv6-analytics

if ! aws athena get-work-group --work-group "$WG" >/dev/null 2>&1; then
  aws athena create-work-group --name "$WG" --configuration '{
    "ResultConfiguration": {"OutputLocation": "s3://topper-stopper-bucket/athena-results/"},
    "EnforceWorkGroupConfiguration": true,
    "PublishCloudWatchMetricsEnabled": true,
    "BytesScannedCutoffPerQuery": 1073741824,
    "EngineVersion": {"SelectedEngineVersion": "Athena engine version 3"}
  }' --description "TSV6 V2 analytics — 1GB/query bytes-scanned cap"
  echo "Created workgroup $WG."
else
  echo "Workgroup $WG exists, skipping."
fi

# Submit the view-creation query in this workgroup. Polls until SUCCEEDED.
QID=$(aws athena start-query-execution --work-group "$WG" \
  --query-string "CREATE OR REPLACE VIEW tsv6.v_scans_v2 AS
    SELECT transactionid, thingname, barcode, scantimestamp, eventtype, returnaction,
           productname, productbrand, productcategory, productimage, containertype,
           containerconfidence, datasource, lookuplatencyms,
           depositplaylist, productplaylist, nomatchplaylist, barcodenotqrplaylist,
           reason, flowversion,
           yyyy AS scan_year, mm AS scan_month, dd AS scan_day, hh AS scan_hour
    FROM tsv6.scans_v2;" \
  --query 'QueryExecutionId' --output text)

echo "Submitted view creation: $QID"
for _ in $(seq 1 30); do
  ST=$(aws athena get-query-execution --query-execution-id "$QID" \
       --query 'QueryExecution.Status.State' --output text)
  case "$ST" in
    SUCCEEDED) echo "View created."; exit 0 ;;
    FAILED|CANCELLED)
      aws athena get-query-execution --query-execution-id "$QID" \
        --query 'QueryExecution.Status.StateChangeReason' --output text
      exit 1 ;;
  esac
  sleep 2
done
echo "View creation timed out." >&2
exit 1
EOF
chmod +x infra/aws/v2/05_athena_workgroup.sh
```

- [ ] **Step 2: Run it**

```bash
bash infra/aws/v2/05_athena_workgroup.sh
```

Expected: `Created workgroup tsv6-analytics.` then `View created.`

- [ ] **Step 3: Verify view**

```bash
aws athena start-query-execution --work-group tsv6-analytics \
  --query-string "SELECT COUNT(*) FROM tsv6.v_scans_v2 LIMIT 1;" \
  --query 'QueryExecutionId' --output text
```

Expected: a query ID is returned. (The query will scan zero bytes — table is empty — and return 0.)

- [ ] **Step 4: Commit**

```bash
git add infra/aws/v2/05_athena_workgroup.sh
git commit -m "infra(v2): Athena workgroup tsv6-analytics + view v_scans_v2"
```

---

### Task 6: IAM role for both V2 Lambdas

**Files:**
- Create: `infra/aws/v2/iam_trust.json`
- Create: `infra/aws/v2/iam_policy.json`
- Create: `infra/aws/v2/06_iam_role.sh`

- [ ] **Step 1: Write the trust policy**

```bash
cat > infra/aws/v2/iam_trust.json <<'EOF'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"lambda.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
```

- [ ] **Step 2: Write the inline policy**

```bash
cat > infra/aws/v2/iam_policy.json <<'EOF'
{
  "Version":"2012-10-17",
  "Statement":[
    {"Sid":"Logs","Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"},
    {"Sid":"Firehose","Effect":"Allow","Action":["firehose:PutRecord","firehose:PutRecordBatch"],
     "Resource":"arn:aws:firehose:us-east-1:010526276861:deliverystream/tsv6-scans-v2"},
    {"Sid":"DDBRead","Effect":"Allow","Action":["dynamodb:GetItem"],
     "Resource":[
       "arn:aws:dynamodb:us-east-1:010526276861:table/master_products",
       "arn:aws:dynamodb:us-east-1:010526276861:table/barcode_negative_cache",
       "arn:aws:dynamodb:us-east-1:010526276861:table/brand_playlists"
     ]},
    {"Sid":"DDBWrite","Effect":"Allow","Action":["dynamodb:PutItem","dynamodb:UpdateItem"],
     "Resource":[
       "arn:aws:dynamodb:us-east-1:010526276861:table/master_products",
       "arn:aws:dynamodb:us-east-1:010526276861:table/barcode_negative_cache"
     ]},
    {"Sid":"S3WebP","Effect":"Allow","Action":["s3:PutObject","s3:PutObjectAcl"],
     "Resource":"arn:aws:s3:::topper-stopper-bucket/product-images-webp/*"},
    {"Sid":"IoTPublish","Effect":"Allow","Action":"iot:Publish",
     "Resource":[
       "arn:aws:iot:us-east-1:010526276861:topic/*/openDoor",
       "arn:aws:iot:us-east-1:010526276861:topic/*/noMatch",
       "arn:aws:iot:us-east-1:010526276861:topic/*/qrCode",
       "arn:aws:iot:us-east-1:010526276861:topic/*/error"
     ]},
    {"Sid":"InvokeUPC","Effect":"Allow","Action":"lambda:InvokeFunction",
     "Resource":"arn:aws:lambda:us-east-1:010526276861:function:UpdatedBarcodeToGoUPCV2"}
  ]
}
EOF
```

- [ ] **Step 3: Write the script**

```bash
cat > infra/aws/v2/06_iam_role.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ROLE=tsv6-lambda-v2-role

if ! aws iam get-role --role-name "$ROLE" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE" \
    --assume-role-policy-document file://infra/aws/v2/iam_trust.json
  echo "Created role $ROLE."
fi

aws iam put-role-policy --role-name "$ROLE" \
  --policy-name tsv6-lambda-v2-inline \
  --policy-document file://infra/aws/v2/iam_policy.json
echo "Policy attached."

# Wait for IAM propagation before any Lambda referencing this role is created.
sleep 10
echo "Done."
EOF
chmod +x infra/aws/v2/06_iam_role.sh
```

- [ ] **Step 4: Run it**

```bash
bash infra/aws/v2/06_iam_role.sh
```

Expected: `Created role tsv6-lambda-v2-role.` then `Policy attached.` then `Done.`

- [ ] **Step 5: Verify**

```bash
aws iam get-role --role-name tsv6-lambda-v2-role --query 'Role.Arn' --output text
aws iam get-role-policy --role-name tsv6-lambda-v2-role --policy-name tsv6-lambda-v2-inline \
  --query 'PolicyDocument.Statement[].Sid'
```

Expected: an ARN string, then `["Logs","Firehose","DDBRead","DDBWrite","S3WebP","IoTPublish","InvokeUPC"]`.

- [ ] **Step 6: Commit**

```bash
git add infra/aws/v2/06_iam_role.sh infra/aws/v2/iam_trust.json infra/aws/v2/iam_policy.json
git commit -m "infra(v2): tsv6-lambda-v2-role with least-privilege inline policy"
```

---

## Phase 2 — Lambda code (test-driven)

### Task 7: BarcodeRepoLookupV2 — scaffolding + first passing test

**Files:**
- Create: `lambdas/barcode_repo_lookup_v2/lambda_function.py`
- Create: `lambdas/barcode_repo_lookup_v2/tests/__init__.py`
- Create: `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py`
- Create: `lambdas/barcode_repo_lookup_v2/requirements.txt`

- [ ] **Step 1: Write the failing test (validation rejects missing fields)**

```bash
mkdir -p lambdas/barcode_repo_lookup_v2/tests
touch     lambdas/barcode_repo_lookup_v2/tests/__init__.py
echo ""  > lambdas/barcode_repo_lookup_v2/requirements.txt
```

```python
# lambdas/barcode_repo_lookup_v2/tests/test_lambda.py
import os, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(autouse=True)
def _aws_clients(monkeypatch):
    """Replace boto3 resource/client with mocks so import-time wiring is safe."""
    fake_dynamodb = MagicMock()
    fake_master   = MagicMock(name="master_products")
    fake_negative = MagicMock(name="barcode_negative_cache")
    fake_brand    = MagicMock(name="brand_playlists")
    def table(name):
        return {"master_products": fake_master,
                "barcode_negative_cache": fake_negative,
                "brand_playlists": fake_brand}[name]
    fake_dynamodb.Table.side_effect = table

    fake_iot      = MagicMock(name="iot-data")
    fake_lambda   = MagicMock(name="lambda")
    fake_firehose = MagicMock(name="firehose")

    def fake_resource(name, **_):
        if name == "dynamodb": return fake_dynamodb
        raise AssertionError(name)
    def fake_client(name, **_):
        return {"iot-data": fake_iot, "lambda": fake_lambda, "firehose": fake_firehose}[name]

    monkeypatch.setattr("boto3.resource", fake_resource)
    monkeypatch.setattr("boto3.client",   fake_client)

    yield {
        "master":   fake_master, "negative": fake_negative, "brand": fake_brand,
        "iot":      fake_iot,    "lambda":   fake_lambda,   "firehose": fake_firehose,
    }

def _import():
    """Import lambda_function fresh after monkeypatch is in place."""
    if "lambda_function" in sys.modules: del sys.modules["lambda_function"]
    import lambda_function
    return lambda_function

def test_missing_barcode_returns_500(_aws_clients):
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X"}, None)
    assert resp["statusCode"] == 500
    assert "barcode" in resp.get("error", "").lower()
```

- [ ] **Step 2: Run the test, confirm it fails**

```bash
cd lambdas/barcode_repo_lookup_v2 && /home/g1tech/tsrpi7/tsrpi5/.venv/bin/python -m pytest tests/test_lambda.py -v
```

Expected: collection error (`No module named 'lambda_function'`) — the file doesn't exist.

- [ ] **Step 3: Write the minimum implementation that makes the test pass**

```python
# lambdas/barcode_repo_lookup_v2/lambda_function.py
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

LAMBDA_NAME    = "BarcodeRepoLookupV2"
LAMBDA_VERSION = os.getenv("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST")
FIREHOSE_NAME  = "tsv6-scans-v2"
UPC_LAMBDA     = "UpdatedBarcodeToGoUPCV2"

DEFAULT_NO_ITEM        = "tsv6_no_item_detected"
DEFAULT_NO_MATCH       = "tsv6_no_match"
DEFAULT_BARCODE_NOT_QR = "tsv6_barcode_not_qr"
DEFAULT_DEPOSIT        = "tsv6_processing"
DEFAULT_PRODUCT        = "tsv6_product_display"

dynamodb = boto3.resource("dynamodb")
iot      = boto3.client("iot-data")
lambda_c = boto3.client("lambda")
firehose = boto3.client("firehose")

master_table   = dynamodb.Table("master_products")
negative_table = dynamodb.Table("barcode_negative_cache")
brand_table    = dynamodb.Table("brand_playlists")


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        return super().default(o)


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _publish(topic, payload):
    iot.publish(topic=topic, qos=1, payload=json.dumps(payload, cls=DecimalEncoder))


def _firehose_put(row):
    firehose.put_record(
        DeliveryStreamName=FIREHOSE_NAME,
        Record={"Data": (json.dumps(row, cls=DecimalEncoder) + "\n").encode()},
    )


def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode")
    thing   = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())

    try:
        if not barcode or not thing:
            raise ValueError("Missing required field: barcode and thingName are required")
    except Exception as e:
        err = {"statusCode": 500, "thingName": thing, "transactionId": txid, "error": str(e)}
        return err

    return {"statusCode": 200, "transactionId": txid}  # placeholder, fleshed out in later tasks
```

- [ ] **Step 4: Run the test, confirm pass**

```bash
cd lambdas/barcode_repo_lookup_v2 && /home/g1tech/tsrpi7/tsrpi5/.venv/bin/python -m pytest tests/test_lambda.py -v
```

Expected: `1 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add lambdas/barcode_repo_lookup_v2/
git commit -m "feat(lambda-v2): scaffold BarcodeRepoLookupV2 + validation test"
```

---

### Task 8: BarcodeRepoLookupV2 — QR detection branch

**Files:**
- Modify: `lambdas/barcode_repo_lookup_v2/lambda_function.py`
- Modify: `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_lambda.py`:

```python
def test_qr_detection_publishes_qrCode_topic(_aws_clients):
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X", "barcode": "https://example.com/foo", "transactionId": "tx1"}, None)
    assert resp["returnAction"] == "QRcode"
    args, kwargs = _aws_clients["iot"].publish.call_args
    assert kwargs["topic"] == "TS_X/qrCode"
    body = __import__("json").loads(kwargs["payload"])
    assert body["barcodeNotQrPlaylist"] == "tsv6_barcode_not_qr"
    _aws_clients["firehose"].put_record.assert_called_once()
    fh_body = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh_body["eventtype"] == "qr_detected"
    assert fh_body["returnaction"] == "QRcode"
```

- [ ] **Step 2: Run, confirm fail**

Expected: `KeyError` or `AssertionError` (current handler returns placeholder).

- [ ] **Step 3: Implement QR branch**

Replace the placeholder return at the bottom of `lambda_handler` with:

```python
    if "http://" in barcode or "https://" in barcode:
        payload = {
            "statusCode": 200, "returnAction": "QRcode",
            "thingName": thing, "transactionId": txid,
            "barcodeNotQrPlaylist": DEFAULT_BARCODE_NOT_QR,
        }
        _publish(f"{thing}/qrCode", payload)
        _firehose_put(_row(
            txid=txid, thing=thing, barcode=barcode,
            event_type="qr_detected", return_action="QRcode",
            barcode_not_qr_playlist=DEFAULT_BARCODE_NOT_QR,
            latency_ms=int((time.time() - started) * 1000),
        ))
        return payload
```

Add a helper just above `lambda_handler`:

```python
def _row(*, txid, thing, barcode, event_type, return_action, latency_ms,
         product_name=None, product_brand=None, product_category=None, product_desc=None,
         product_image=None, product_image_original=None, container_type=None,
         container_confidence=None, data_source=None, qr_url=None,
         deposit_playlist=None, product_playlist=None, no_item_playlist=None,
         no_match_playlist=None, barcode_not_qr_playlist=None, reason=None):
    return {
        "transactionid": txid, "thingname": thing, "barcode": barcode,
        "scantimestamp": _now_iso(), "eventtype": event_type, "returnaction": return_action,
        "productname": product_name, "productbrand": product_brand,
        "productcategory": product_category, "productdesc": product_desc,
        "productimage": product_image, "productimageoriginal": product_image_original,
        "containertype": container_type, "containerconfidence": container_confidence,
        "datasource": data_source, "lookuplatencyms": latency_ms,
        "qrurl": qr_url,
        "depositplaylist": deposit_playlist, "productplaylist": product_playlist,
        "noitemplaylist": no_item_playlist, "nomatchplaylist": no_match_playlist,
        "barcodenotqrplaylist": barcode_not_qr_playlist,
        "reason": reason, "flowversion": "v2",
        "lambdaname": LAMBDA_NAME, "lambdaversion": LAMBDA_VERSION,
    }
```

- [ ] **Step 4: Run, confirm pass**

```bash
cd lambdas/barcode_repo_lookup_v2 && /home/g1tech/tsrpi7/tsrpi5/.venv/bin/python -m pytest tests/test_lambda.py -v
```

Expected: `2 passed`.

- [ ] **Step 5: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add lambdas/barcode_repo_lookup_v2/
git commit -m "feat(lambda-v2): QR detection branch in BarcodeRepoLookupV2"
```

---

### Task 9: BarcodeRepoLookupV2 — master_products hit branch

**Files:**
- Modify: `lambdas/barcode_repo_lookup_v2/lambda_function.py`
- Modify: `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py`

- [ ] **Step 1: Add brand-resolver helper**

Just above `lambda_handler`:

```python
def _resolve_brand_playlists(brand):
    item = brand_table.get_item(Key={"brand": brand or "*default*"}).get("Item")
    if not item:
        item = brand_table.get_item(Key={"brand": "*default*"}).get("Item") or {}
    return (
        item.get("depositPlaylist", DEFAULT_DEPOSIT),
        item.get("productPlaylist", DEFAULT_PRODUCT),
    )
```

- [ ] **Step 2: Write the failing test**

```python
def test_master_hit_publishes_openDoor_with_webp(_aws_clients):
    _aws_clients["master"].get_item.return_value = {"Item": {
        "barcode": "611269163452",
        "productName": "Red Bull Yellow",
        "productBrand": "Red Bull",
        "productCategory": "Beverages",
        "productDesc": "Tropical energy drink",
        "productImage":      "https://go-upc.s3.amazonaws.com/images/93437582.png",
        "productImageWebp":  "https://topper-stopper-bucket.s3.amazonaws.com/product-images-webp/611269163452.webp",
        "productImageOriginal": "https://go-upc.s3.amazonaws.com/images/93437582.png",
        "containerType": "can", "containerConfidence": Decimal("0.95"),
    }}
    _aws_clients["brand"].get_item.return_value = {"Item": {
        "brand": "Red Bull",
        "depositPlaylist": "tsv6_redbull_promo",
        "productPlaylist": "tsv6_redbull_product",
    }}
    lf = _import()
    resp = lf.lambda_handler({"thingName": "TS_X", "barcode": "611269163452", "transactionId": "tx2"}, None)
    assert resp["returnAction"] == "openDoor"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/openDoor"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["productImage"].endswith("/611269163452.webp")
    assert body["productImageOriginal"].endswith(".png")
    assert body["depositPlaylist"]   == "tsv6_redbull_promo"
    assert body["productPlaylist"]   == "tsv6_redbull_product"
    assert body["noItemPlaylist"]    == "tsv6_no_item_detected"
    assert body["qrUrl"].startswith("https://tsrewards--test.expo.app/hook?scanid=tx2&barcode=611269163452")
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"] == "master_hit"
    assert fh["datasource"] == "master"
```

Add at top of test file: `from decimal import Decimal`.

- [ ] **Step 3: Run, confirm fail**

- [ ] **Step 4: Implement the master-hit branch**

Insert after the QR block in `lambda_handler`:

```python
    item = master_table.get_item(Key={"barcode": barcode}).get("Item")
    if item:
        deposit_pl, product_pl = _resolve_brand_playlists(item.get("productBrand"))
        wire_image = item.get("productImageWebp") or item.get("productImage")
        qr_url = f"https://tsrewards--test.expo.app/hook?scanid={txid}&barcode={barcode}"
        payload = {
            "statusCode": 200, "returnAction": "openDoor",
            "thingName": thing, "transactionId": txid, "barcode": barcode,
            "productName":     item.get("productName"),
            "productBrand":    item.get("productBrand"),
            "productCategory": item.get("productCategory"),
            "productDesc":     item.get("productDesc"),
            "productImage":    wire_image,
            "productImageOriginal": item.get("productImageOriginal") or item.get("productImage"),
            "containerType":   item.get("containerType"),
            "containerConfidence": float(item.get("containerConfidence", 0) or 0),
            "qrUrl": qr_url,
            "depositPlaylist": deposit_pl,
            "productPlaylist": product_pl,
            "noItemPlaylist":  DEFAULT_NO_ITEM,
            "dataSource": "master",
        }
        _publish(f"{thing}/openDoor", payload)
        _firehose_put(_row(
            txid=txid, thing=thing, barcode=barcode,
            event_type="master_hit", return_action="openDoor",
            product_name=payload["productName"], product_brand=payload["productBrand"],
            product_category=payload["productCategory"], product_desc=payload["productDesc"],
            product_image=payload["productImage"], product_image_original=payload["productImageOriginal"],
            container_type=payload["containerType"], container_confidence=payload["containerConfidence"],
            data_source="master", qr_url=qr_url,
            deposit_playlist=deposit_pl, product_playlist=product_pl,
            no_item_playlist=DEFAULT_NO_ITEM,
            latency_ms=int((time.time() - started) * 1000),
        ))
        return payload
```

- [ ] **Step 5: Run, confirm pass**

Expected: `3 passed`.

- [ ] **Step 6: Commit**

```bash
git add lambdas/barcode_repo_lookup_v2/
git commit -m "feat(lambda-v2): master_products hit branch with brand playlist resolution"
```

---

### Task 10: BarcodeRepoLookupV2 — negative cache + cold-path fallthrough branches

**Files:**
- Modify: `lambdas/barcode_repo_lookup_v2/lambda_function.py`
- Modify: `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_negative_cache_hit_publishes_noMatch(_aws_clients):
    from datetime import datetime, timedelta, timezone
    _aws_clients["master"].get_item.return_value = {}  # miss
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    _aws_clients["negative"].get_item.return_value = {"Item": {"barcode": "999", "expires_at": future}}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"999","transactionId":"tx3"}, None)
    assert resp["returnAction"] == "noMatch"
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/noMatch"
    body = __import__("json").loads(pub[1]["payload"])
    assert body["reason"] == "cached_nomatch"
    assert body["noMatchPlaylist"] == "tsv6_no_match"

def test_full_miss_invokes_upc_lambda(_aws_clients):
    _aws_clients["master"].get_item.return_value   = {}
    _aws_clients["negative"].get_item.return_value = {}
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"888","transactionId":"tx4"}, None)
    assert resp["returnAction"] == "forwardedToUPC"
    inv = _aws_clients["lambda"].invoke.call_args
    assert inv[1]["FunctionName"] == "UpdatedBarcodeToGoUPCV2"
    assert inv[1]["InvocationType"] == "Event"
    payload = __import__("json").loads(inv[1]["Payload"])
    assert payload == {"barcode":"888","thingName":"TS_X","transactionId":"tx4"}
    # No Firehose row from V1-side on the miss path; UPC lambda writes it.
    _aws_clients["firehose"].put_record.assert_not_called()
```

- [ ] **Step 2: Run, confirm fail**

- [ ] **Step 3: Implement both branches**

Insert after the master-hit branch in `lambda_handler`:

```python
    neg = negative_table.get_item(Key={"barcode": barcode}).get("Item")
    if neg:
        valid = True
        if "expires_at" in neg:
            try:
                exp = datetime.fromisoformat(neg["expires_at"].replace("Z", "+00:00"))
                valid = datetime.now(timezone.utc) < exp
            except Exception:
                valid = True
        if valid:
            payload = {
                "statusCode": 200, "returnAction": "noMatch",
                "thingName": thing, "transactionId": txid, "barcode": barcode,
                "reason": "cached_nomatch",
                "noMatchPlaylist": DEFAULT_NO_MATCH,
            }
            _publish(f"{thing}/noMatch", payload)
            _firehose_put(_row(
                txid=txid, thing=thing, barcode=barcode,
                event_type="nomatch_cached", return_action="noMatch",
                no_match_playlist=DEFAULT_NO_MATCH, reason="cached_nomatch",
                latency_ms=int((time.time() - started) * 1000),
            ))
            return payload

    lambda_c.invoke(
        FunctionName=UPC_LAMBDA, InvocationType="Event",
        Payload=json.dumps({"barcode": barcode, "thingName": thing, "transactionId": txid}),
    )
    return {"statusCode": 200, "returnAction": "forwardedToUPC",
            "thingName": thing, "transactionId": txid, "barcode": barcode}
```

- [ ] **Step 4: Run, confirm pass**

Expected: `5 passed`.

- [ ] **Step 5: Commit**

```bash
git add lambdas/barcode_repo_lookup_v2/
git commit -m "feat(lambda-v2): negative-cache and UPC-fallthrough branches"
```

---

### Task 11: BarcodeRepoLookupV2 — global error path

**Files:**
- Modify: `lambdas/barcode_repo_lookup_v2/lambda_function.py`
- Modify: `lambdas/barcode_repo_lookup_v2/tests/test_lambda.py`

- [ ] **Step 1: Write the failing test**

```python
def test_internal_exception_publishes_error_topic(_aws_clients):
    _aws_clients["master"].get_item.side_effect = RuntimeError("boom")
    lf = _import()
    resp = lf.lambda_handler({"thingName":"TS_X","barcode":"777","transactionId":"tx5"}, None)
    assert resp["statusCode"] == 500
    pub = _aws_clients["iot"].publish.call_args
    assert pub[1]["topic"] == "TS_X/error"
    fh = __import__("json").loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"]    == "lambda_error"
    assert fh["returnaction"] == "error"
```

- [ ] **Step 2: Wrap the body in try/except**

Refactor `lambda_handler` so everything after validation is inside one try/except that publishes `{thing}/error` and writes a `lambda_error` Firehose row before returning a 500.

```python
def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode")
    thing   = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())

    if not barcode or not thing:
        return {"statusCode": 500, "thingName": thing, "transactionId": txid,
                "error": "Missing required field: barcode and thingName are required"}
    try:
        # ... existing branches: QR, master, negative, fallthrough ...
        return _process(event, started, barcode, thing, txid)
    except Exception as e:
        _publish(f"{thing}/error", {"statusCode": 500, "thingName": thing,
                                    "transactionId": txid, "error": str(e)})
        try:
            _firehose_put(_row(
                txid=txid, thing=thing, barcode=barcode,
                event_type="lambda_error", return_action="error",
                reason=str(e),
                latency_ms=int((time.time() - started) * 1000),
            ))
        except Exception:
            pass
        return {"statusCode": 500, "thingName": thing, "transactionId": txid, "error": str(e)}
```

Move all branches into `_process(event, started, barcode, thing, txid)`.

- [ ] **Step 3: Run all tests, confirm pass**

Expected: `6 passed`.

- [ ] **Step 4: Commit**

```bash
git add lambdas/barcode_repo_lookup_v2/
git commit -m "feat(lambda-v2): global error handler with error topic + firehose row"
```

---

### Task 12: BarcodeRepoLookupV2 — package + deploy

**Files:**
- Create: `lambdas/barcode_repo_lookup_v2/deploy.sh`

- [ ] **Step 1: Write the deploy script**

```bash
cat > lambdas/barcode_repo_lookup_v2/deploy.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
NAME=BarcodeRepoLookupV2
ROLE_ARN=$(aws iam get-role --role-name tsv6-lambda-v2-role --query 'Role.Arn' --output text)
ZIP=/tmp/${NAME}.zip
rm -f "$ZIP"
zip -j "$ZIP" lambda_function.py >/dev/null
if aws lambda get-function --function-name "$NAME" >/dev/null 2>&1; then
  aws lambda update-function-code --function-name "$NAME" --zip-file "fileb://$ZIP" >/dev/null
  aws lambda update-function-configuration --function-name "$NAME" \
    --runtime python3.12 --timeout 20 --memory-size 256 --role "$ROLE_ARN" >/dev/null
  echo "Updated $NAME."
else
  aws lambda create-function --function-name "$NAME" \
    --runtime python3.12 --timeout 20 --memory-size 256 \
    --role "$ROLE_ARN" --handler lambda_function.lambda_handler \
    --zip-file "fileb://$ZIP" >/dev/null
  echo "Created $NAME."
fi
EOF
chmod +x lambdas/barcode_repo_lookup_v2/deploy.sh
```

- [ ] **Step 2: Deploy**

```bash
bash lambdas/barcode_repo_lookup_v2/deploy.sh
```

Expected: `Created BarcodeRepoLookupV2.`

- [ ] **Step 3: Smoke test the deployed function with a synthetic event**

```bash
aws lambda invoke --function-name BarcodeRepoLookupV2 \
  --payload '{"thingName":"TS_TEST","barcode":"https://example.com"}' \
  --cli-binary-format raw-in-base64-out /tmp/out.json
cat /tmp/out.json
```

Expected: `{"statusCode":200,"returnAction":"QRcode","thingName":"TS_TEST", ...}`. (CloudWatch Logs will show the IoT publish.)

- [ ] **Step 4: Commit**

```bash
git add lambdas/barcode_repo_lookup_v2/deploy.sh
git commit -m "feat(lambda-v2): deploy script for BarcodeRepoLookupV2"
```

---

### Task 13: UpdatedBarcodeToGoUPCV2 — scaffolding + GoUPC lookup

**Files:**
- Create: `lambdas/updated_barcode_to_go_upc_v2/lambda_function.py`
- Create: `lambdas/updated_barcode_to_go_upc_v2/tests/__init__.py`
- Create: `lambdas/updated_barcode_to_go_upc_v2/tests/test_lambda.py`
- Create: `lambdas/updated_barcode_to_go_upc_v2/requirements.txt`

- [ ] **Step 1: Layout**

```bash
mkdir -p lambdas/updated_barcode_to_go_upc_v2/tests
touch     lambdas/updated_barcode_to_go_upc_v2/tests/__init__.py
echo 'requests' > lambdas/updated_barcode_to_go_upc_v2/requirements.txt
```

- [ ] **Step 2: Write the failing test for GoUPC happy path**

```python
# lambdas/updated_barcode_to_go_upc_v2/tests/test_lambda.py
import os, sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from unittest.mock import MagicMock, patch
import pytest

@pytest.fixture(autouse=True)
def _aws_clients(monkeypatch):
    fake_ddb = MagicMock()
    fake_master   = MagicMock(name="master_products")
    fake_negative = MagicMock(name="barcode_negative_cache")
    fake_brand    = MagicMock(name="brand_playlists")
    fake_brand.get_item.return_value = {"Item": {"depositPlaylist":"tsv6_processing","productPlaylist":"tsv6_product_display"}}
    fake_ddb.Table.side_effect = lambda n: {"master_products":fake_master,"barcode_negative_cache":fake_negative,"brand_playlists":fake_brand}[n]
    fake_iot      = MagicMock(); fake_firehose = MagicMock(); fake_s3 = MagicMock()
    monkeypatch.setattr("boto3.resource", lambda name, **_: fake_ddb)
    monkeypatch.setattr("boto3.client",   lambda name, **_: {"iot-data":fake_iot,"firehose":fake_firehose,"s3":fake_s3}[name])
    monkeypatch.setenv("GO_UPC_API_KEY", "test-key")
    yield {"master":fake_master,"negative":fake_negative,"brand":fake_brand,"iot":fake_iot,"firehose":fake_firehose,"s3":fake_s3}

def _import():
    if "lambda_function" in sys.modules: del sys.modules["lambda_function"]
    import lambda_function
    return lambda_function

def test_goupc_resolved_publishes_openDoor_with_null_image(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc",  return_value={"name":"Foo","brand":"Bar","category":"Beverages","imageUrl":"https://x/y.png"}), \
         patch.object(lf, "_convert_and_upload_webp", return_value="https://topper-stopper-bucket.s3.amazonaws.com/product-images-webp/123.webp"):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"123","transactionId":"tx1"}, None)
    assert resp["returnAction"] == "openDoor"
    pub = _aws_clients["iot"].publish.call_args
    body = json.loads(pub[1]["payload"])
    assert body["productImage"] is None              # first scan: no image
    assert body["productImageOriginal"] == "https://x/y.png"
    assert body["productName"]  == "Foo"
    assert body["dataSource"]   == "go_upc"
    fh = json.loads(_aws_clients["firehose"].put_record.call_args[1]["Record"]["Data"])
    assert fh["eventtype"]   == "upc_resolved"
    assert fh["datasource"]  == "go_upc"
    # WebP put should also write master_products
    _aws_clients["master"].put_item.assert_called_once()
    written = _aws_clients["master"].put_item.call_args[1]["Item"]
    assert written["barcode"] == "123"
    assert written["productImageWebp"].endswith("/123.webp")
    assert written["productImage"] == "https://x/y.png"   # source URL stays in productImage
```

- [ ] **Step 3: Run, confirm fail**

- [ ] **Step 4: Write the implementation**

```python
# lambdas/updated_barcode_to_go_upc_v2/lambda_function.py
import io, json, os, time, urllib.request, uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

LAMBDA_NAME    = "UpdatedBarcodeToGoUPCV2"
LAMBDA_VERSION = os.getenv("AWS_LAMBDA_FUNCTION_VERSION", "$LATEST")
FIREHOSE_NAME  = "tsv6-scans-v2"
WEBP_BUCKET    = "topper-stopper-bucket"
WEBP_PREFIX    = "product-images-webp"
NEGATIVE_TTL_DAYS = 30
MAX_IMAGE_BYTES = 5 * 1024 * 1024

DEFAULT_NO_ITEM        = "tsv6_no_item_detected"
DEFAULT_NO_MATCH       = "tsv6_no_match"
DEFAULT_DEPOSIT        = "tsv6_processing"
DEFAULT_PRODUCT        = "tsv6_product_display"

dynamodb = boto3.resource("dynamodb")
iot      = boto3.client("iot-data")
firehose = boto3.client("firehose")
s3       = boto3.client("s3")
master_table   = dynamodb.Table("master_products")
negative_table = dynamodb.Table("barcode_negative_cache")
brand_table    = dynamodb.Table("brand_playlists")

GO_UPC_API_KEY = os.environ.get("GO_UPC_API_KEY", "")
GO_UPC_API_URL = "https://go-upc.com/api/v1/code"


class DecimalEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, Decimal): return float(o)
        return super().default(o)


def _now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _publish(topic, payload):
    iot.publish(topic=topic, qos=1, payload=json.dumps(payload, cls=DecimalEncoder))


def _firehose_put(row):
    firehose.put_record(DeliveryStreamName=FIREHOSE_NAME,
                        Record={"Data": (json.dumps(row, cls=DecimalEncoder)+"\n").encode()})


def _row(**k):
    base = {
        "transactionid": k["txid"], "thingname": k["thing"], "barcode": k.get("barcode"),
        "scantimestamp": _now_iso(), "eventtype": k["event_type"], "returnaction": k["return_action"],
        "productname": k.get("product_name"), "productbrand": k.get("product_brand"),
        "productcategory": k.get("product_category"), "productdesc": k.get("product_desc"),
        "productimage": k.get("product_image"), "productimageoriginal": k.get("product_image_original"),
        "containertype": k.get("container_type"), "containerconfidence": k.get("container_confidence"),
        "datasource": k.get("data_source"), "lookuplatencyms": k.get("latency_ms"),
        "qrurl": k.get("qr_url"), "depositplaylist": k.get("deposit_playlist"),
        "productplaylist": k.get("product_playlist"), "noitemplaylist": k.get("no_item_playlist"),
        "nomatchplaylist": k.get("no_match_playlist"), "barcodenotqrplaylist": k.get("barcode_not_qr_playlist"),
        "reason": k.get("reason"), "flowversion": "v2",
        "lambdaname": LAMBDA_NAME, "lambdaversion": LAMBDA_VERSION,
    }
    return base


def _resolve_brand_playlists(brand):
    item = brand_table.get_item(Key={"brand": brand or "*default*"}).get("Item")
    if not item:
        item = brand_table.get_item(Key={"brand": "*default*"}).get("Item") or {}
    return (item.get("depositPlaylist", DEFAULT_DEPOSIT),
            item.get("productPlaylist", DEFAULT_PRODUCT))


def _fetch_goupc(barcode):
    """Return {'name','brand','category','imageUrl'} or None."""
    if not GO_UPC_API_KEY: return None
    import urllib.request
    req = urllib.request.Request(f"{GO_UPC_API_URL}/{barcode}?key={GO_UPC_API_KEY}",
                                 headers={"User-Agent": "tsv6-v2"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode())
    except Exception:
        return None
    p = data.get("product") or {}
    if not (p.get("name") or p.get("brand")): return None
    return {"name": p.get("name"), "brand": p.get("brand"),
            "category": p.get("category"), "imageUrl": p.get("imageUrl")}


def _convert_and_upload_webp(barcode, source_url):
    """Download source, convert to WebP, upload. Returns final WebP URL or None on any failure."""
    try:
        from PIL import Image
        with urllib.request.urlopen(source_url, timeout=5) as r:
            buf = r.read(MAX_IMAGE_BYTES + 1)
        if len(buf) > MAX_IMAGE_BYTES: return None
        img = Image.open(io.BytesIO(buf)).convert("RGB")
        out = io.BytesIO()
        img.save(out, "WEBP", quality=80, method=6)
        key = f"{WEBP_PREFIX}/{barcode}.webp"
        s3.put_object(Bucket=WEBP_BUCKET, Key=key, Body=out.getvalue(),
                      ContentType="image/webp",
                      CacheControl="public, max-age=31536000, immutable")
        return f"https://{WEBP_BUCKET}.s3.amazonaws.com/{key}"
    except Exception:
        return None


def _publish_no_match(thing, txid, barcode, reason, started):
    payload = {"statusCode":200,"returnAction":"noMatch","thingName":thing,
               "transactionId":txid,"barcode":barcode,"reason":reason,
               "noMatchPlaylist":DEFAULT_NO_MATCH}
    _publish(f"{thing}/noMatch", payload)
    _firehose_put(_row(txid=txid, thing=thing, barcode=barcode,
                       event_type=("upc_nomatch" if reason=="upc_nomatch" else "upc_error"),
                       return_action="noMatch", reason=reason,
                       no_match_playlist=DEFAULT_NO_MATCH,
                       latency_ms=int((time.time()-started)*1000)))
    return payload


def lambda_handler(event, _ctx):
    started = time.time()
    barcode = event.get("barcode"); thing = event.get("thingName")
    txid    = event.get("transactionId") or event.get("transactionID") or str(uuid.uuid4())
    if not barcode or not thing:
        return {"statusCode":500,"thingName":thing,"transactionId":txid,"error":"Missing barcode/thingName"}

    try:
        result = _fetch_goupc(barcode)  # extend with fallbacks in Task 14
        if not result:
            negative_table.put_item(Item={"barcode": barcode,
                "expires_at": (datetime.now(timezone.utc)+timedelta(days=NEGATIVE_TTL_DAYS)).isoformat().replace("+00:00","Z"),
                "source": "upc_nomatch"})
            return _publish_no_match(thing, txid, barcode, "upc_nomatch", started)

        deposit_pl, product_pl = _resolve_brand_playlists(result.get("brand"))
        qr_url = f"https://tsrewards--test.expo.app/hook?scanid={txid}&barcode={barcode}"
        payload = {
            "statusCode":200, "returnAction":"openDoor",
            "thingName":thing, "transactionId":txid, "barcode":barcode,
            "productName":     result.get("name"),
            "productBrand":    result.get("brand"),
            "productCategory": result.get("category"),
            "productDesc":     None,
            "productImage":    None,                       # first scan = text only
            "productImageOriginal": result.get("imageUrl"),
            "containerType":   None,
            "containerConfidence": None,
            "qrUrl": qr_url,
            "depositPlaylist": deposit_pl,
            "productPlaylist": product_pl,
            "noItemPlaylist":  DEFAULT_NO_ITEM,
            "dataSource": "go_upc",
        }
        _publish(f"{thing}/openDoor", payload)
        _firehose_put(_row(txid=txid, thing=thing, barcode=barcode,
            event_type="upc_resolved", return_action="openDoor",
            product_name=payload["productName"], product_brand=payload["productBrand"],
            product_category=payload["productCategory"],
            product_image=None, product_image_original=payload["productImageOriginal"],
            data_source="go_upc", qr_url=qr_url,
            deposit_playlist=deposit_pl, product_playlist=product_pl,
            no_item_playlist=DEFAULT_NO_ITEM,
            latency_ms=int((time.time()-started)*1000)))

        webp_url = _convert_and_upload_webp(barcode, payload["productImageOriginal"])
        master_item = {
            "barcode": barcode,
            "productName": payload["productName"], "productBrand": payload["productBrand"],
            "productCategory": payload["productCategory"], "productDesc": payload["productDesc"],
            "productImage":    payload["productImageOriginal"],   # JPEG/PNG for V1 reads
            "productImageOriginal": payload["productImageOriginal"],
        }
        if webp_url: master_item["productImageWebp"] = webp_url
        master_table.put_item(Item=master_item)
        return payload
    except Exception as e:
        try:
            return _publish_no_match(thing, txid, barcode, "upc_error", started)
        except Exception:
            return {"statusCode":500,"thingName":thing,"transactionId":txid,"error":str(e)}
```

- [ ] **Step 5: Run, confirm pass**

```bash
cd lambdas/updated_barcode_to_go_upc_v2 && /home/g1tech/tsrpi7/tsrpi5/.venv/bin/python -m pytest tests/test_lambda.py -v
```

Expected: `1 passed`.

- [ ] **Step 6: Commit**

```bash
cd /home/g1tech/tsrpi7/tsrpi5
git add lambdas/updated_barcode_to_go_upc_v2/
git commit -m "feat(lambda-v2): UpdatedBarcodeToGoUPCV2 happy path with WebP write-through"
```

---

### Task 14: UpdatedBarcodeToGoUPCV2 — fallback chain, nomatch, error paths

**Files:**
- Modify: `lambdas/updated_barcode_to_go_upc_v2/lambda_function.py`
- Modify: `lambdas/updated_barcode_to_go_upc_v2/tests/test_lambda.py`

The remaining flows mirror the V1 fallback chain. Lift the upcitemdb / OpenFoodFacts / USDA helpers from `/tmp/UpdatedBarcodeToGoUPC/lambda_function.py` (already downloaded in this session via `aws lambda get-function`), simplify to plain `urllib.request`, drop the V1 image-download/Rekognition step (V2 does WebP, not Rekognition; container-type is left null on cold path and can be backfilled later).

- [ ] **Step 1: Failing tests**

```python
def test_upc_nomatch_writes_negative_cache(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", return_value=None), \
         patch.object(lf, "_fetch_upcitemdb", return_value=None), \
         patch.object(lf, "_fetch_openfoodfacts", return_value=None), \
         patch.object(lf, "_fetch_usda", return_value=None):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"000","transactionId":"tx2"}, None)
    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "upc_nomatch"
    _aws_clients["negative"].put_item.assert_called_once()

def test_upc_error_does_not_write_negative_cache(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", side_effect=RuntimeError("boom")):
        resp = lf.lambda_handler({"thingName":"TS_X","barcode":"222","transactionId":"tx3"}, None)
    assert resp["returnAction"] == "noMatch"
    assert resp["reason"] == "upc_error"
    _aws_clients["negative"].put_item.assert_not_called()

def test_image_conversion_failure_skips_webp_field(_aws_clients):
    lf = _import()
    with patch.object(lf, "_fetch_goupc", return_value={"name":"X","brand":"Y","category":"Z","imageUrl":"https://broken/"}), \
         patch.object(lf, "_convert_and_upload_webp", return_value=None):
        lf.lambda_handler({"thingName":"TS_X","barcode":"333","transactionId":"tx4"}, None)
    written = _aws_clients["master"].put_item.call_args[1]["Item"]
    assert "productImageWebp" not in written
    assert written["productImage"] == "https://broken/"
```

- [ ] **Step 2: Run, confirm fails**

- [ ] **Step 3: Implement the fallback chain and tighten error paths**

Add helpers `_fetch_upcitemdb`, `_fetch_openfoodfacts`, `_fetch_usda` (each returns the same dict shape or `None`). Wrap them with sequential calls in `lambda_handler` so the first non-None wins; track `data_source` accordingly.

Add a small wrapper around the GoUPC call so a raised exception goes through the `upc_error` path rather than being swallowed in `_fetch_goupc`. The cleanest split: `_fetch_goupc` returns `None` on data-not-found, but raises on network/parse exceptions. `lambda_handler` catches `Exception` and calls `_publish_no_match(..., "upc_error", ...)` without writing the negative cache.

- [ ] **Step 4: Run, confirm pass**

Expected: `4 passed`.

- [ ] **Step 5: Commit**

```bash
git add lambdas/updated_barcode_to_go_upc_v2/
git commit -m "feat(lambda-v2): UPC fallback chain + cache-safe error path"
```

---

### Task 15: UpdatedBarcodeToGoUPCV2 — package + Pillow layer + deploy

**Files:**
- Create: `lambdas/updated_barcode_to_go_upc_v2/deploy.sh`

The Lambda needs Pillow. Use the public `klayers` Pillow layer for python3.12. The current ARN can be looked up at https://api.klayers.cloud/api/v2/p3.12/layers/latest/us-east-1/ — pin a specific version in the deploy script after first lookup.

- [ ] **Step 1: Look up the layer ARN once**

```bash
curl -s https://api.klayers.cloud/api/v2/p3.12/layers/latest/us-east-1/json | jq '.[] | select(.package == "Pillow")'
```

Capture the `arn` field; paste it into the deploy script as `PILLOW_LAYER_ARN`.

- [ ] **Step 2: Write the deploy script**

```bash
cat > lambdas/updated_barcode_to_go_upc_v2/deploy.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
NAME=UpdatedBarcodeToGoUPCV2
ROLE_ARN=$(aws iam get-role --role-name tsv6-lambda-v2-role --query 'Role.Arn' --output text)
PILLOW_LAYER_ARN="<<PASTE FROM klayers LOOKUP>>"

# Bundle code + requests into a zip (Pillow comes from the layer).
WORK=$(mktemp -d); trap "rm -rf $WORK" EXIT
cp lambda_function.py "$WORK"/
/home/g1tech/tsrpi7/tsrpi5/.venv/bin/python -m pip install -r requirements.txt -t "$WORK" --quiet
ZIP=/tmp/${NAME}.zip; rm -f "$ZIP"
( cd "$WORK" && zip -r "$ZIP" . >/dev/null )

# GO_UPC_API_KEY must already be set in the deploying shell or via SSM/secrets — read from env.
ENV_VARS="Variables={GO_UPC_API_KEY=${GO_UPC_API_KEY:?Set GO_UPC_API_KEY in the env before deploy}}"

if aws lambda get-function --function-name "$NAME" >/dev/null 2>&1; then
  aws lambda update-function-code   --function-name "$NAME" --zip-file "fileb://$ZIP" >/dev/null
  aws lambda update-function-configuration --function-name "$NAME" \
    --runtime python3.12 --timeout 30 --memory-size 512 --role "$ROLE_ARN" \
    --layers "$PILLOW_LAYER_ARN" --environment "$ENV_VARS" >/dev/null
  echo "Updated $NAME."
else
  aws lambda create-function --function-name "$NAME" \
    --runtime python3.12 --timeout 30 --memory-size 512 \
    --role "$ROLE_ARN" --handler lambda_function.lambda_handler \
    --zip-file "fileb://$ZIP" \
    --layers "$PILLOW_LAYER_ARN" --environment "$ENV_VARS" >/dev/null
  echo "Created $NAME."
fi
EOF
chmod +x lambdas/updated_barcode_to_go_upc_v2/deploy.sh
```

- [ ] **Step 3: Source GoUPC key**

The V1 Lambda already had `GO_UPC_API_KEY` set. Read it from V1 and re-use it:

```bash
export GO_UPC_API_KEY=$(aws lambda get-function-configuration \
  --function-name UpdatedBarcodeToGoUPC \
  --query 'Environment.Variables.GO_UPC_API_KEY' --output text)
```

- [ ] **Step 4: Deploy**

```bash
bash lambdas/updated_barcode_to_go_upc_v2/deploy.sh
```

Expected: `Created UpdatedBarcodeToGoUPCV2.`

- [ ] **Step 5: Smoke test**

```bash
aws lambda invoke --function-name UpdatedBarcodeToGoUPCV2 \
  --payload '{"thingName":"TS_TEST","barcode":"000000000000"}' \
  --cli-binary-format raw-in-base64-out /tmp/out.json
cat /tmp/out.json
```

Expected: `{"statusCode":200,"returnAction":"noMatch","reason":"upc_nomatch", ...}` (a 12-zero barcode will fail every fallback).

- [ ] **Step 6: Commit**

```bash
git add lambdas/updated_barcode_to_go_upc_v2/deploy.sh
git commit -m "feat(lambda-v2): deploy script for UpdatedBarcodeToGoUPCV2 with Pillow layer"
```

---

## Phase 3 — IoT routing

### Task 16: IoT rule barcodeRepoLookupV2 + V1 rule update

**Files:**
- Create: `infra/aws/v2/07_iot_rule.sh`

- [ ] **Step 1: Write the script**

```bash
cat > infra/aws/v2/07_iot_rule.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
ACCT=010526276861

# 1. Allow IoT to invoke the V2 Lambda.
for FN in BarcodeRepoLookupV2; do
  aws lambda add-permission --function-name $FN \
    --statement-id iot-invoke-${FN}-$(date +%s) \
    --action lambda:InvokeFunction --principal iot.amazonaws.com \
    --source-arn arn:aws:iot:us-east-1:${ACCT}:rule/barcodeRepoLookupV2 2>/dev/null || true
done

# 2. Create the V2 rule.
RULE=barcodeRepoLookupV2
PAYLOAD=$(cat <<JSON
{
  "sql":"SELECT topic(3) as thingName, state.reported.barcode as barcode, state.reported.transactionID as transactionID FROM '\$aws/things/+/shadow/update' WHERE state.reported.flowVersion = 'v2' AND state.reported.barcode <> ''",
  "ruleDisabled": false,
  "awsIotSqlVersion": "2016-03-23",
  "actions": [
    {"lambda": {"functionArn": "arn:aws:lambda:us-east-1:${ACCT}:function:BarcodeRepoLookupV2"}}
  ]
}
JSON
)

if aws iot get-topic-rule --rule-name "$RULE" >/dev/null 2>&1; then
  aws iot replace-topic-rule --rule-name "$RULE" --topic-rule-payload "$PAYLOAD"
  echo "Replaced rule $RULE."
else
  aws iot create-topic-rule  --rule-name "$RULE" --topic-rule-payload "$PAYLOAD"
  echo "Created  rule $RULE."
fi

# 3. Update V1 rule: add flowVersion <> 'v2' guard and drop dead sb_BarcodeRepoLookup action.
V1=barcodeRepoLookup
V1_PAYLOAD=$(cat <<JSON
{
  "sql":"SELECT topic(3) as thingName, state.reported.barcode as barcode, state.reported.transactionID as transactionID FROM '\$aws/things/+/shadow/update' WHERE state.reported.barcode <> '' AND (isUndefined(state.reported.flowVersion) = true OR state.reported.flowVersion <> 'v2')",
  "ruleDisabled": false,
  "awsIotSqlVersion": "2016-03-23",
  "actions": [
    {"lambda": {"functionArn": "arn:aws:lambda:us-east-1:${ACCT}:function:BarcodeRepoLookup"}}
  ]
}
JSON
)
aws iot replace-topic-rule --rule-name "$V1" --topic-rule-payload "$V1_PAYLOAD"
echo "Updated rule $V1 (V2 traffic excluded; dead sb_BarcodeRepoLookup action removed)."
echo "Done."
EOF
chmod +x infra/aws/v2/07_iot_rule.sh
```

- [ ] **Step 2: Run it**

```bash
bash infra/aws/v2/07_iot_rule.sh
```

Expected: `Created rule barcodeRepoLookupV2.` then `Updated rule barcodeRepoLookup ...` then `Done.`

- [ ] **Step 3: Verify both rules**

```bash
aws iot get-topic-rule --rule-name barcodeRepoLookupV2 --query 'rule.{sql:sql,actions:actions}'
aws iot get-topic-rule --rule-name barcodeRepoLookup   --query 'rule.{sql:sql,actions:actions}'
```

Expected:
- V2 rule shows the `flowVersion = 'v2'` filter and one Lambda action.
- V1 rule shows the `flowVersion <> 'v2'` guard and only the `BarcodeRepoLookup` action (no `sb_BarcodeRepoLookup`).

- [ ] **Step 4: Verify a V1 device still routes correctly**

Find any one V1 thing (anything other than `TS_EFFC94AA`) and inspect the most recent CloudWatch log group `/aws/lambda/BarcodeRepoLookup` to confirm it still receives invocations after the rule swap. The simplest non-invasive check is just to leave it for the canary verification.

- [ ] **Step 5: Commit**

```bash
git add infra/aws/v2/07_iot_rule.sh
git commit -m "infra(v2): IoT rule barcodeRepoLookupV2 + V1 rule guard + dead action removal"
```

---

## Phase 4 — Device-side wiring

### Task 17: Add `flowVersion` to shadow updates

**Files:**
- Modify: `src/tsv6/core/main.py`
- Modify: `src/tsv6/core/production_main.py` (only if it has its own publish path; otherwise skip)

- [ ] **Step 1: Inspect current publish path**

```bash
grep -n '"reported":' src/tsv6/core/main.py
```

You should see the `state.reported` dict at `main.py:200-210` (`publish_to_aws_iot`).

- [ ] **Step 2: Edit `main.py`**

Add one line inside the `"reported": { ... }` dict, e.g. just after `"thingName": thing_name`:

```python
                            "thingName": thing_name,
                            "flowVersion": "v2",
```

- [ ] **Step 3: Verify other publish paths**

```bash
grep -rn '\$aws/things/.*shadow/update\|"reported"' src/tsv6/ --include='*.py' | grep -v test
```

If `production_main.py` or `aws_resilient_manager.py` has its own publish path with a `reported` dict, add `"flowVersion": "v2"` there too.

- [ ] **Step 4: Verify nothing else broke**

```bash
./.venv/bin/python -m pytest tests/unit/ -q
```

Expected: same pass rate as before the change.

- [ ] **Step 5: Commit**

```bash
git add src/tsv6/core/main.py src/tsv6/core/production_main.py 2>/dev/null || true
git commit -m "feat(device): set flowVersion=v2 in shadow updates to opt into V2 cloud path"
```

---

### Task 18: `playlist_override` kwarg on three PiSignageAdapter methods

**Files:**
- Modify: `src/tsv6/display/pisignage_adapter.py`
- Modify: `src/tsv6/display/controller.py`
- Modify: `src/tsv6/display/tsv6_player/backend.py`
- Modify: `tests/unit/test_pisignage_adapter.py`

- [ ] **Step 1: Failing tests for the three methods**

```python
# tests/unit/test_pisignage_adapter.py — add to TestPiSignageAdapterConvenienceMethods

@patch("requests.post")
def test_show_no_match_with_override(self, mock_post, connected_adapter):
    mock_post.return_value.status_code = 200
    connected_adapter.show_no_match(playlist_override="tsv6_redbull_no_match")
    assert "tsv6_redbull_no_match" in mock_post.call_args[0][0]

@patch("requests.post")
def test_show_no_item_detected_with_override(self, mock_post, connected_adapter):
    mock_post.return_value.status_code = 200
    connected_adapter.show_no_item_detected(playlist_override="tsv6_pepsi_no_item")
    assert "tsv6_pepsi_no_item" in mock_post.call_args[0][0]

@patch("requests.post")
def test_show_barcode_not_qr_with_override(self, mock_post, connected_adapter):
    mock_post.return_value.status_code = 200
    connected_adapter.show_barcode_not_qr(playlist_override="tsv6_alt_qr_warn")
    assert "tsv6_alt_qr_warn" in mock_post.call_args[0][0]

@patch("requests.post")
def test_show_no_match_uses_default_when_override_invalid(self, mock_post, connected_adapter):
    mock_post.return_value.status_code = 200
    connected_adapter.show_no_match(playlist_override="../etc/passwd")
    assert "tsv6_no_match" in mock_post.call_args[0][0]
```

- [ ] **Step 2: Run, confirm fail**

```bash
./.venv/bin/python -m pytest tests/unit/test_pisignage_adapter.py -v -k "with_override or uses_default"
```

Expected: TypeError (`unexpected keyword argument 'playlist_override'`).

- [ ] **Step 3: Modify `pisignage_adapter.py`**

Locate `show_no_match`, `show_no_item_detected`, `show_barcode_not_qr` (around lines 302–310). Each currently looks like:

```python
def show_no_match(self) -> bool:
    return self.switch_playlist(self._config.no_match_playlist)
```

Change to:

```python
def show_no_match(self, playlist_override: str | None = None) -> bool:
    name = self._resolve_playlist(playlist_override, self._config.no_match_playlist)
    return self.switch_playlist(name)
```

Apply the same pattern to the other two methods, using `self._config.no_item_playlist` and `self._config.barcode_not_qr_playlist`.

- [ ] **Step 4: Update the `DisplayController` Protocol**

In `src/tsv6/display/controller.py`, find the three method signatures and add `playlist_override: str | None = None` to each, mirroring the existing pattern used by `show_deposit_item` and `show_product_display`.

- [ ] **Step 5: Update `TSV6NativeBackend`**

In `src/tsv6/display/tsv6_player/backend.py`, the same three methods accept `playlist_override` but ignore it (no per-call playlist concept on the native backend). Add the kwarg to the signatures so the protocol matches.

- [ ] **Step 6: Run all unit tests**

```bash
./.venv/bin/python -m pytest tests/unit/ -q
```

Expected: all pass, including the four new tests.

- [ ] **Step 7: Commit**

```bash
git add src/tsv6/display/pisignage_adapter.py src/tsv6/display/controller.py \
        src/tsv6/display/tsv6_player/backend.py tests/unit/test_pisignage_adapter.py
git commit -m "feat(display): playlist_override on show_no_match/show_no_item_detected/show_barcode_not_qr"
```

---

### Task 19: Thread cloud playlist overrides in `production_main.py`

**Files:**
- Modify: `src/tsv6/core/production_main.py`

- [ ] **Step 1: Locate the relevant call sites**

```bash
grep -n 'show_no_match\|show_no_item_detected\|show_barcode_not_qr\|noMatchPlaylist\|noItemPlaylist\|barcodeNotQrPlaylist' src/tsv6/core/production_main.py
```

You will see calls like `self.display_backend.show_no_match()` with no override argument today.

- [ ] **Step 2: Cache `noItemPlaylist` from openDoor at door-sequence start**

In `_handle_open_door_command` (or whatever consumes `product_data` for openDoor), grab `product_data.get("noItemPlaylist")` and store on `self._pending_no_item_playlist`. Example:

```python
self._pending_no_item_playlist = product_data.get("noItemPlaylist") if isinstance(product_data, dict) else None
```

- [ ] **Step 3: Pass the cached override on sensor timeout**

Wherever `show_no_item_detected()` is called (most likely inside `_verified_door_sequence` when the recycle sensor times out without detecting a deposit), change to:

```python
self.display_backend.show_no_item_detected(
    playlist_override=getattr(self, "_pending_no_item_playlist", None)
)
```

- [ ] **Step 4: Pass `noMatchPlaylist` from noMatch payloads**

Find the noMatch handler (look at `aws_resilient_manager.no_match_display_callback` registration and the function it calls). Pass `payload.get("noMatchPlaylist")` through to `show_no_match(playlist_override=...)`.

- [ ] **Step 5: Pass `barcodeNotQrPlaylist` from qrCode payloads**

`OptimizedBarcodeScanner.qr_code_callback` is currently invoked with the local barcode text (no AWS payload). The cloud emits `barcodeNotQrPlaylist` on the `{thing}/qrCode` topic. Subscribe to that topic in `aws_resilient_manager._subscribe_to_topics` and route the payload's `barcodeNotQrPlaylist` to a new `display_backend.show_barcode_not_qr(playlist_override=...)`. (V2-only path; V1 doesn't emit `barcodeNotQrPlaylist`, so the override is None and the device falls back to `tsv6_barcode_not_qr`.)

- [ ] **Step 6: Run device test suite**

```bash
./.venv/bin/python -m pytest tests/unit/ tests/integration/ -q
```

Expected: pass (or same baseline failures unrelated to this change).

- [ ] **Step 7: Commit**

```bash
git add src/tsv6/core/production_main.py
git commit -m "feat(device): thread cloud playlist overrides for noItem/noMatch/barcodeNotQr"
```

---

### Task 20: Handle `productImage = None` in image overlay

**Files:**
- Modify: `src/tsv6/core/image_manager.py` (or `main.py` if the overlay logic lives there)

- [ ] **Step 1: Locate the overlay**

```bash
grep -n 'product_image\|productImage\|imageUrl\|_show_image_overlay' src/tsv6/core/image_manager.py src/tsv6/core/main.py | head
```

- [ ] **Step 2: Failing test (or manual check description)**

Synthesize an openDoor payload with `productImage: null` in a unit test against `image_manager.load_image_for_display` (or whatever image fetcher is wired up). Confirm that the overlay still renders product card with name+brand+category and does not crash.

If a unit test isn't practical for this code path, write a one-shot script `scripts/v2_text_only_smoke.py` that constructs a fake openDoor payload and invokes the overlay path, then runs it on the bench Pi. Document the expected behavior (text card, no image area) in the script docstring.

- [ ] **Step 3: Make the overlay tolerate null/empty `productImage`**

Add early-return in the image-loading helper:

```python
def load_image_for_display(self, image_path, target_size, *, master=None, **_):
    if not image_path:
        return None
```

In `_show_image_overlay`, if the loader returns `None`, skip the image label creation and let the existing text labels (productName, productBrand, productCategory) fill the overlay.

- [ ] **Step 4: Run unit tests**

```bash
./.venv/bin/python -m pytest tests/unit/ -q
```

- [ ] **Step 5: Commit**

```bash
git add src/tsv6/core/image_manager.py src/tsv6/core/main.py 2>/dev/null
git commit -m "feat(device): render text-only product card when productImage is null"
```

---

## Phase 5 — Verification

### Task 21: End-to-end smoke test on `TS_EFFC94AA`

**Files:**
- Create: `scripts/v2_smoke_test.py`

- [ ] **Step 1: Write the script**

The bench script `scripts/scan_publish_open.py` already exists from earlier in this session and connects MQTT, scans, and drives the servo. Build a V2-aware variant that:
- Adds `"flowVersion": "v2"` to its shadow-update payload.
- Subscribes to `{thing}/openDoor`, `{thing}/noMatch`, `{thing}/qrCode`, `{thing}/error`.
- Prints the entire received payload (so we can eyeball `depositPlaylist`, `productPlaylist`, `productImage`, etc.).
- Optionally accepts `--barcode <code>` to skip the physical scanner for repeatable testing.

```bash
cp scripts/scan_publish_open.py scripts/v2_smoke_test.py
```

Then edit `scripts/v2_smoke_test.py`:
1. In the shadow-update payload (look for `"reported":` near the bottom of `main()`), add `"flowVersion": "v2"`.
2. Add subscriptions to `{thing}/qrCode` and `{thing}/error` mirroring the existing two.
3. Print the entire JSON payload from each callback (not just the action field).
4. Add `argparse` for `--barcode` to bypass the scanner.

- [ ] **Step 2: Run with the cached Red Bull barcode**

```bash
./.venv/bin/python scripts/v2_smoke_test.py --barcode 611269163452
```

Expected:
- MQTT connect succeeds.
- openDoor arrives within 1–2 s.
- Payload includes `productImage` (likely the legacy JPEG URL since that row pre-exists in `master_products` from V1), `productImageOriginal`, `depositPlaylist: tsv6_processing` (from `*default*` row), `productPlaylist: tsv6_product_display`, `noItemPlaylist: tsv6_no_item_detected`, `qrUrl`.
- Servo open/close.

- [ ] **Step 3: Run with a brand-new barcode (cold UPC path)**

Pick any 12-digit barcode unlikely to be in master_products (e.g., `077341125532` — a Heinz Ketchup). First scan:

```bash
./.venv/bin/python scripts/v2_smoke_test.py --barcode 077341125532
```

Expected first time:
- openDoor arrives in 3–8 s (UPC chain).
- `productImage: null`, `productImageOriginal: <go-upc URL>`, `productName/productBrand` populated.
- Servo opens, but device renders text-only card.

Run it again:

```bash
./.venv/bin/python scripts/v2_smoke_test.py --barcode 077341125532
```

Expected second time:
- openDoor arrives in <1 s (now in master_products with WebP).
- `productImage` ends with `/077341125532.webp`.

- [ ] **Step 4: Run with a URL "barcode" (QR detection)**

```bash
./.venv/bin/python scripts/v2_smoke_test.py --barcode "https://tsrewards.example.com/foo"
```

Expected:
- qrCode topic publishes within 1–2 s.
- Payload includes `barcodeNotQrPlaylist: tsv6_barcode_not_qr`.

- [ ] **Step 5: Athena verification (after 5 min Firehose buffer)**

```bash
sleep 300
QID=$(aws athena start-query-execution --work-group tsv6-analytics \
  --query-string "SELECT eventtype, datasource, productname, barcode FROM tsv6.v_scans_v2 WHERE thingname='TS_EFFC94AA' ORDER BY scantimestamp DESC LIMIT 10;" \
  --query 'QueryExecutionId' --output text)
sleep 5
aws athena get-query-results --query-execution-id "$QID" \
  --query 'ResultSet.Rows[*].Data[*].VarCharValue'
```

Expected: rows for the three test scans, with `eventtype` of `master_hit`, `upc_resolved`, and `qr_detected`.

- [ ] **Step 6: Commit**

```bash
git add scripts/v2_smoke_test.py
git commit -m "test(v2): end-to-end smoke script with flowVersion=v2 + Athena verification"
```

---

### Task 22: Document the V2 cutover

**Files:**
- Modify: `docs/superpowers/specs/2026-04-25-barcode-repo-lookup-v2-design.md` (add a "Status: Implemented" line)
- Optional: Create a short ops note in `docs/` describing how to add a brand to `brand_playlists`.

- [ ] **Step 1: Update spec status**

Add to the top of the spec file, just under `**Status:**`:

```
**Status:** Implemented at commit <SHA> (canary on TS_EFFC94AA, fleet-wide migration deferred).
```

- [ ] **Step 2: Optional ops note**

Write `docs/V2_BRAND_PLAYLISTS.md` with the `aws dynamodb put-item` template for adding a sponsor brand to `brand_playlists`.

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "docs(v2): mark spec implemented + brand_playlists ops note"
```

---

## Self-review

**Spec coverage** (mapping spec sections to tasks):

| Spec section                      | Implementing task(s)         |
|-----------------------------------|------------------------------|
| §3 high-level flow                | Tasks 7–11, 13–14            |
| §4 routing/flowVersion            | Tasks 16, 17                 |
| §5 BarcodeRepoLookupV2            | Tasks 7–12                   |
| §6 brand_playlists                | Tasks 1, 9, 13               |
| §7.1 openDoor payload             | Tasks 9, 13                  |
| §7.2 noMatch payload              | Tasks 10, 14                 |
| §7.3 qrCode payload               | Task 8                       |
| §7.4 error payload                | Task 11                      |
| §8 UpdatedBarcodeToGoUPCV2        | Tasks 13–15                  |
| §9 Firehose/S3/Athena             | Tasks 2, 3, 4, 5             |
| §10 WebP pipeline (productImage / productImageWebp split) | Task 13 (impl), Task 14 (failure case) |
| §11 device-side changes           | Tasks 17, 18, 19, 20         |
| §12 IAM                           | Task 6                       |
| §14 testing                       | Tasks 7–11, 13–14, 21        |
| §15 cutover                       | Tasks 16, 17, 21             |
| §16 rollback                      | (Operational; documented in spec — disable rule, remove flag) |

**Placeholder scan**: Task 15 Step 1 has a `<<PASTE FROM klayers LOOKUP>>` placeholder that the executor must fill in after the lookup. This is acceptable because the value is determined at execution time and the surrounding step explicitly tells the executor where the value comes from. No other placeholders found.

**Type/name consistency**: `productImageWebp` (DDB field) vs `productImage` (wire payload field) is consistent throughout — Tasks 9, 13, 14 all reflect the spec §10 separation. `flowVersion: "v2"` is consistent in Tasks 16, 17, 21. Lambda function names `BarcodeRepoLookupV2` / `UpdatedBarcodeToGoUPCV2` consistent across Tasks 7–16.

**Scope check**: 22 tasks producing AWS infra (6) + Lambda code (9) + IoT routing (1) + device code (4) + verification (2). Single coherent feature, single implementation plan.
