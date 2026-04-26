# BarcodeRepoLookupV2 + UpdatedBarcodeToGoUPCV2 — Design

**Date:** 2026-04-25
**Status:** Implemented at commit `035b533` on branch `feat/barcode-repo-lookup-v2` (canary on TS_EFFC94AA verified 2026-04-26: master_hit / upc_nomatch / nomatch_cached / qr_detected all land in `tsv6.v_scans_v2`; fleet-wide migration deferred — V1 rule still routes any device that does not publish `flowVersion="v2"`).
**Author:** g1tech + Claude
**Scope:** AWS-side rebuild of the barcode lookup pipeline plus minimal device-side wiring to consume the new payload fields. V1 fleet remains untouched.

## 1. Goals

1. Cut hot-path Lambda execution from ~250 ms (S3 read-modify-write + DDB Pilot_Scans put) to ~10 ms (single Firehose put).
2. Replace the unbounded `all_preprod_scans.json` history file with a partitioned, columnar (Parquet) S3 store queryable via Athena.
3. Provide a stable analytics contract (Athena view) for Zoho's API connector.
4. Convert product images to WebP once at first cold-path resolution, so V2 devices fetch lighter images over LTE.
5. Let the cloud drive PiSignage playlist selection per-brand and per-event-type, with safe device-side defaults.
6. Keep V1 path completely unchanged so the existing fleet is unaffected during co-existence.
7. Keep AWS steady-state cost under $2/month at 10k scans/day.

## 2. Non-Goals

- Migrating the existing fleet onto V2 in this spec. (Cutover is per-device by setting `flowVersion: "v2"` in the shadow update; mass migration is a follow-up.)
- Replacing or migrating data already in `Pilot_Scans` or `all_preprod_scans.json`. V1 sinks keep running for V1 devices.
- DAX, ElastiCache, or any other read-cache in front of `master_products`. Existing GetItem latency is sufficient.
- CloudFront / Lambda@Edge image delivery. WebP at S3 origin is enough for now; CDN is a future optimization.
- DynamoDB `Pilot_Scans` writes from V2. V2 devices' analytics live entirely in Firehose → S3 → Athena.
- NFC. V2 emits `qrUrl` only; `nfcUrl` is removed from the V2 payload.

## 3. High-Level Flow

```
┌───────────────┐   shadow update          ┌──────────────────────┐
│  Device (V2)  │ ───────────────────────▶ │ IoT Rule             │
│ flowVersion=  │  state.reported.{        │ barcodeRepoLookupV2  │
│   "v2"        │    barcode, transactionID│ WHERE flowVersion='v2'│
│               │    flowVersion="v2",...} └──────┬───────────────┘
└───────────────┘                                 │ invoke
                                                  ▼
                                          ┌────────────────────┐
                                          │ BarcodeRepoLookupV2│
                                          │  Lambda            │
                                          └──┬─────────────────┘
                          ┌──QR──────────────┤
                          ▼                  │
              publish {thing}/qrCode         ├──master hit──▶ publish openDoor
              + firehose row                 │                + firehose row
                                             │
                                             ├──nomatch cache hit──▶ publish noMatch
                                             │                       + firehose row
                                             │
                                             └──miss──▶ invoke UpdatedBarcodeToGoUPCV2
                                                            │
                                                            ▼
                                                ┌─────────────────────────┐
                                                │ UpdatedBarcodeToGoUPCV2 │
                                                │ Lambda                  │
                                                └──┬──────────────────────┘
                                                   │
                                  ┌─upc resolved───┤
                                  ▼                ├──upc_nomatch──▶ publish noMatch
                              publish openDoor     │                 + firehose row
                              with productImage:null│                + negative_cache put
                              (first scan)          │
                              + firehose row        └──upc_error──▶ publish noMatch
                              + (background) WebP                   + firehose row
                              + master_products put
```

All Firehose rows land in `s3://topper-stopper-bucket/scans-v2/yyyy/mm/dd/hh/...parquet`.

## 4. Routing — `flowVersion` Feature Flag

**Device side**: `production_main.py` (and `main.py` for non-production runs) adds a single field to the `state.reported` shadow update:

```python
"reported": {
    ...,
    "flowVersion": "v2",
    "barcode": barcode_data,
    "transactionID": transaction_id,
    ...
}
```

**AWS side**: two IoT topic rules on the same source topic:

- `barcodeRepoLookupV2` — `SELECT topic(3) as thingName, state.reported.barcode as barcode, state.reported.transactionID as transactionID FROM '$aws/things/+/shadow/update' WHERE state.reported.flowVersion = 'v2' AND state.reported.barcode <> ''`. Actions: invoke `BarcodeRepoLookupV2`.
- `barcodeRepoLookup` (existing V1, modified) — same SELECT but WHERE clause becomes `state.reported.barcode <> '' AND (isUndefined(state.reported.flowVersion) OR state.reported.flowVersion <> 'v2')`. Actions unchanged: `BarcodeRepoLookup` and the dead `sb_BarcodeRepoLookup` reference (we will also clean up that dead action as part of this work).

A device flips to V2 by setting one field in shadow updates. Flipping back is the same change reversed. Per-device canary is trivial.

## 5. `BarcodeRepoLookupV2` Lambda

**Runtime**: python3.12, 256 MB, 20 s timeout. Handler `lambda_function.lambda_handler`.

**Hot-path steps:**

1. Validate `barcode` and `thingName` present; resolve `transactionId` (accept `transactionId` or `transactionID`; generate UUID v4 if neither present).
2. **QR code detection**: if `barcode` contains `http://` or `https://`:
   - Look up `*default*` row in `brand_playlists` to get `barcodeNotQrPlaylist` (defaults to `tsv6_barcode_not_qr` if row absent).
   - Publish `{thing}/qrCode` with `{statusCode:200, returnAction:"QRcode", thingName, transactionId, barcodeNotQrPlaylist}`.
   - Write Firehose row (`eventType: qr_detected`, `returnAction: QRcode`).
   - Return.
3. **Master products lookup**: `master_products.get_item(barcode)`.
   - **Hit**: build openDoor payload (see §7.1), look up brand playlists (§6), publish `{thing}/openDoor`, write Firehose row (`eventType: master_hit`, `dataSource: master`), return.
4. **Negative cache lookup**: `barcode_negative_cache.get_item(barcode)`.
   - **Hit and not expired**: build noMatch payload (see §7.2), publish `{thing}/noMatch` with `reason: "cached_nomatch"`, write Firehose row (`eventType: nomatch_cached`, `returnAction: noMatch`), return.
5. **Miss**: `lambda_client.invoke(FunctionName="UpdatedBarcodeToGoUPCV2", InvocationType="Event", Payload={barcode, thingName, transactionId})`. Return `{statusCode:200, returnAction:"forwardedToUPC", thingName, transactionId, barcode}`. **No Firehose row from V2 here** — UpdatedBarcodeToGoUPCV2 will write the resolved/error row when it completes.

**Hot-path data writes**: exactly one Firehose `put_record` per terminal event type. Zero DynamoDB writes (negative cache writes happen in UpdatedBarcodeToGoUPCV2). Zero S3 reads/writes.

**Errors**: any unhandled exception → publish `{thing}/error` with the exception string, write Firehose row (`eventType: lambda_error`, `returnAction: error`), return error response. Never crash without telling the device something.

## 6. `brand_playlists` Lookup

DynamoDB table, on-demand billing:

```
brand (PK, S)        | depositPlaylist (S)        | productPlaylist (S)
"Red Bull"           | "tsv6_redbull_promo"       | "tsv6_redbull_product"
"Pepsi"              | "tsv6_pepsi_promo"         | "tsv6_pepsi_product"
"*default*"          | "tsv6_processing"          | "tsv6_product_display"
```

The `*default*` row is mandatory and seeded at table creation. It exists so brand-not-found is a single GetItem fallback rather than two round-trips.

**Lookup logic** (used in §5 step 3 and in §8 cold path on resolved):

```python
def resolve_brand_playlists(brand: str) -> tuple[str, str]:
    item = brand_table.get_item(Key={"brand": brand}).get("Item")
    if item is None:
        item = brand_table.get_item(Key={"brand": "*default*"}).get("Item") or {}
    return (
        item.get("depositPlaylist", "tsv6_processing"),
        item.get("productPlaylist", "tsv6_product_display"),
    )
```

**Static playlists** (not brand-dependent) are returned as constants in the payload directly:

- `noItemPlaylist`: `"tsv6_no_item_detected"` — included in every openDoor payload so device can use it on sensor timeout.
- `noMatchPlaylist`: `"tsv6_no_match"` — included in every noMatch payload.
- `barcodeNotQrPlaylist`: `"tsv6_barcode_not_qr"` — included in every qrCode payload.

These are constants in the Lambda code initially. If you ever want to remap them per-brand (e.g., a sponsored "no item detected" video for Red Bull), promote them to `brand_playlists` columns later. The schema change is additive.

## 7. Response Payloads

### 7.1 openDoor

```json
{
  "statusCode": 200,
  "returnAction": "openDoor",
  "thingName": "TS_EFFC94AA",
  "transactionId": "...",
  "barcode": "611269163452",
  "productName": "Red Bull The Yellow Edition Tropical Energy Drink",
  "productBrand": "Red Bull",
  "productCategory": "Beverages",
  "productDesc": "...",
  "productImage": "https://topper-stopper-bucket.s3.amazonaws.com/product-images-webp/611269163452.webp",
  "productImageOriginal": "https://go-upc.s3.amazonaws.com/images/93437582.png",
  "containerType": "can",
  "containerConfidence": 0.95,
  "qrUrl": "https://tsrewards--test.expo.app/hook?scanid=<transactionId>&barcode=611269163452",
  "depositPlaylist": "tsv6_redbull_promo",
  "productPlaylist": "tsv6_redbull_product",
  "noItemPlaylist": "tsv6_no_item_detected",
  "dataSource": "master"
}
```

**On first-ever cold scan** of a brand-new product: `productImage: null` (device renders text-only product card), `productImageOriginal: <source url>`, `dataSource: "go_upc"` (or fallback name). All subsequent scans of the same barcode are master_products hits and carry the WebP URL.

### 7.2 noMatch

```json
{
  "statusCode": 200,
  "returnAction": "noMatch",
  "thingName": "TS_EFFC94AA",
  "transactionId": "...",
  "barcode": "...",
  "reason": "cached_nomatch | upc_nomatch | upc_error",
  "noMatchPlaylist": "tsv6_no_match"
}
```

### 7.3 qrCode

```json
{
  "statusCode": 200,
  "returnAction": "QRcode",
  "thingName": "TS_EFFC94AA",
  "transactionId": "...",
  "barcodeNotQrPlaylist": "tsv6_barcode_not_qr"
}
```

### 7.4 error

```json
{
  "statusCode": 500,
  "thingName": "TS_EFFC94AA",
  "transactionId": "...",
  "error": "<exception message>"
}
```

## 8. `UpdatedBarcodeToGoUPCV2` Lambda

**Runtime**: python3.12, 512 MB, 30 s timeout. Pillow added via Lambda layer (use the public `klayers` Pillow layer for python3.12 to avoid maintaining a build step).

**Steps:**

1. Receive `{barcode, thingName, transactionId}` from `BarcodeRepoLookupV2.invoke`.
2. Try lookups in order: GoUPC → upcitemdb → OpenFoodFacts → USDA. Same fallback chain as V1's UpdatedBarcodeToGoUPC. (Code can be lifted from V1 with minor refactoring.)
3. **If a result is found** with at least a `productName` or `productBrand`:
   - Resolve brand playlists via §6.
   - Build openDoor payload (see §7.1) with `productImage: null` and `productImageOriginal: <source url>`.
   - Publish `{thing}/openDoor`.
   - Write Firehose row (`eventType: upc_resolved`, `returnAction: openDoor`, `dataSource: go_upc | upcitemdb | openfoodfacts | usda`, `lookupLatencyMs: ...`).
   - **Background** (after publish):
     - Download source image (timeout 5s).
     - `PIL.Image.open(buf).convert("RGB").save(out, "WEBP", quality=80, method=6)`.
     - `s3.put_object(Bucket="topper-stopper-bucket", Key=f"product-images-webp/{barcode}.webp", Body=out, ContentType="image/webp", CacheControl="public, max-age=31536000, immutable")`.
     - `master_products.put_item({barcode, productName, productBrand, productCategory, productDesc, productImage: <webp url>, productImageOriginal: <source url>, containerType, containerConfidence, ...})`.
   - If image download/conversion fails, store `productImage: <source url>` (graceful degradation — V2 device can still load the JPEG/PNG, just larger).
4. **If no result found**: publish `{thing}/noMatch` with `reason: "upc_nomatch"`, write Firehose row (`eventType: upc_nomatch`, `returnAction: noMatch`), `barcode_negative_cache.put_item({barcode, expires_at: <now+30d>, source: "upc_nomatch"})`.
5. **On any internal error**: publish `{thing}/noMatch` with `reason: "upc_error"`, write Firehose row (`eventType: upc_error`, `returnAction: noMatch`). Do **not** add to negative cache (transient errors should not poison the cache).

**Container-type filtering**: V1 marks beverage products with non-beverage container types as noMatch. V2 keeps this behavior — same Rekognition logic, same downstream signal. The Firehose row records the actual `containerType` and `containerConfidence` regardless, so analytics can see what was rejected.

## 9. Firehose / S3 / Athena

### 9.1 Firehose stream `tsv6-scans-v2`

- Source: direct PUT (Lambda calls `firehose_client.put_record`).
- Buffer hints: 300 s **or** 128 MB, whichever comes first.
- Format conversion: enabled, JSON → Parquet (Snappy compression), schema from Glue table `tsv6.scans_v2`.
- Destination: `s3://topper-stopper-bucket/scans-v2/`.
- Dynamic partitioning enabled with prefix `yyyy=!{partitionKeyFromQuery:year}/mm=!{partitionKeyFromQuery:month}/dd=!{partitionKeyFromQuery:day}/hh=!{partitionKeyFromQuery:hour}/`.
- Error output: `s3://topper-stopper-bucket/scans-v2-errors/`.

### 9.2 S3 lifecycle policy

On prefix `scans-v2/`:
- 0–30 days: Standard.
- 30–90 days: Standard-IA.
- 90 days–2 years: Glacier Instant Retrieval (still queryable from Athena).
- 2 years+: expire.

On prefix `product-images-webp/`: no lifecycle (immutable, pinned forever; serves as the master cache).
On prefix `scans-v2-errors/`: 30-day expiration.

### 9.3 Glue table `tsv6.scans_v2`

Partitioned (`yyyy STRING, mm STRING, dd STRING, hh STRING`), Parquet, Snappy, location `s3://topper-stopper-bucket/scans-v2/`. Columns:

| Column                  | Type       | Notes                                                              |
|-------------------------|------------|--------------------------------------------------------------------|
| transactionId           | string     | UUID v4                                                            |
| thingName               | string     | e.g., `TS_EFFC94AA`                                                |
| barcode                 | string     |                                                                    |
| scanTimestamp           | timestamp  | UTC ISO-8601                                                       |
| eventType               | string     | `master_hit | nomatch_cached | upc_resolved | upc_nomatch | upc_error | qr_detected | lambda_error` |
| returnAction            | string     | `openDoor | noMatch | QRcode | error`                              |
| productName             | string     | nullable                                                           |
| productBrand            | string     | nullable                                                           |
| productCategory         | string     | nullable                                                           |
| productDesc             | string     | nullable                                                           |
| productImage            | string     | nullable; WebP URL or null on first scan                           |
| productImageOriginal    | string     | nullable; source JPEG/PNG URL                                      |
| containerType           | string     | nullable                                                           |
| containerConfidence     | double     | nullable                                                           |
| dataSource              | string     | `master | go_upc | upcitemdb | openfoodfacts | usda | none`        |
| lookupLatencyMs         | int        | end-to-end Lambda latency (excludes IoT publish ack)               |
| qrUrl                   | string     | nullable                                                           |
| depositPlaylist         | string     | nullable for noMatch/qrCode/error rows                             |
| productPlaylist         | string     | nullable                                                           |
| noItemPlaylist          | string     | nullable                                                           |
| noMatchPlaylist         | string     | nullable                                                           |
| barcodeNotQrPlaylist    | string     | nullable                                                           |
| reason                  | string     | nullable; populated on noMatch/error                               |
| flowVersion             | string     | always `"v2"` for V2-emitted rows                                  |
| lambdaName              | string     | `BarcodeRepoLookupV2 | UpdatedBarcodeToGoUPCV2`                    |
| lambdaVersion           | string     | function version (e.g., `"$LATEST"` or numeric)                    |

Year/month/day/hour partition keys are derived from `scanTimestamp` via Firehose dynamic partitioning JQ expressions:
```
year=.scanTimestamp[0:4]
month=.scanTimestamp[5:7]
day=.scanTimestamp[8:10]
hour=.scanTimestamp[11:13]
```

### 9.4 Athena workgroup `tsv6-analytics`

- Result location: `s3://topper-stopper-bucket/athena-results/`.
- Per-query bytes scanned cutoff: 1 GB (queries exceeding this are aborted).
- Engine version: 3.

### 9.5 Athena view `tsv6.v_scans_v2`

The stable contract for Zoho's API connector. Lives in workgroup `tsv6-analytics`. It re-aliases columns and exposes a flat shape so we can evolve the underlying Parquet schema without breaking analytics.

```sql
CREATE OR REPLACE VIEW tsv6.v_scans_v2 AS
SELECT
    transactionId,
    thingName,
    barcode,
    scanTimestamp,
    eventType,
    returnAction,
    productName,
    productBrand,
    productCategory,
    productImage,
    containerType,
    containerConfidence,
    dataSource,
    lookupLatencyMs,
    depositPlaylist,
    productPlaylist,
    noMatchPlaylist,
    barcodeNotQrPlaylist,
    reason,
    flowVersion,
    yyyy AS scan_year,
    mm AS scan_month,
    dd AS scan_day,
    hh AS scan_hour
FROM tsv6.scans_v2;
```

Zoho Analytics points at this view via Athena's JDBC connector (60s polling cadence is fine; Firehose buffer is 5 min so consecutive polls see incremental rows).

## 10. WebP Image Pipeline

**Trigger**: only `UpdatedBarcodeToGoUPCV2` cold-path resolves convert images. Master hits never re-convert (they read whatever WebP URL is already in `master_products.productImageWebp`).

**`master_products` field separation (V1-safe)**:

- `productImage` — V1 contract. JPEG/PNG source URL. V1 Lambda reads this; V1 device decodes JPEG/PNG. **V2 Lambdas write only the source URL here, never WebP.**
- `productImageWebp` — V2-only field. WebP URL on `topper-stopper-bucket/product-images-webp/`. Null/absent for rows V1 created or rows where conversion failed.
- `productImageOriginal` — V2-only field, redundant copy of source URL kept for reference and re-conversion.

V2 Lambda **read path** for master hits: prefer `productImageWebp`; if absent, fall back to `productImage` (JPEG/PNG); the resolved URL is what V2 emits as `productImage` in the **wire payload**. This means V1 devices reading shared rows continue to see JPEG/PNG via the V1 Lambda; V2 devices reading the same rows see WebP if available, JPEG/PNG otherwise.

**Conversion flow** (cold path):
1. Source URL comes back from GoUPC (or fallback). Common formats: JPEG, PNG, occasionally GIF.
2. Lambda fetches the source via `urllib.request.urlopen(timeout=5)`. Cap response at 5 MB to prevent abuse.
3. `PIL.Image.open(BytesIO(data)).convert("RGB").save(out, "WEBP", quality=80, method=6)`.
   - `quality=80`: visually lossless for product photos at typical display sizes.
   - `method=6`: slowest/best compression (~50 ms per image at 512MB Lambda memory; one-time per product).
4. Upload to `s3://topper-stopper-bucket/product-images-webp/{barcode}.webp` with `Cache-Control: public, max-age=31536000, immutable` and `ContentType: image/webp`.
5. `master_products.put_item` (or `update_item`) writes: `productImage` = source URL, `productImageWebp` = WebP URL, `productImageOriginal` = source URL (same as productImage; kept distinct for forward-compat).

**On conversion failure** (corrupt source, unsupported format, size limit, network error): catch the exception, log it, write `productImage` = source URL and **omit** `productImageWebp` from the put. V2 future reads then fall back to the source URL. Never block openDoor on image conversion.

**Public access**: `product-images-webp/` prefix needs public-read or a presigned URL strategy. Per the existing pattern (V1 stores `go-upc.s3.amazonaws.com` URLs that the device fetches directly), public-read is consistent. Document the bucket policy change in the implementation plan.

## 11. Device-Side Changes (`tsrpi5`)

Single PR, isolated to V2 wiring. V1 fleet sees identical behavior since all changes are additive with safe fallbacks.

### 11.1 `production_main.py` and `main.py`: emit `flowVersion`

Add `"flowVersion": "v2"` to the `state.reported` dict in `publish_to_aws_iot` (or wherever the shadow update payload is built). One-line change.

### 11.2 `pisignage_adapter.py`: add `playlist_override` to three more methods

Currently only `show_deposit_item` and `show_product_display` accept `playlist_override`. Mirror that pattern for:

- `show_no_match(playlist_override: str | None = None)` — uses override or `self._config.no_match_playlist`.
- `show_no_item_detected(playlist_override: str | None = None)` — uses override or `self._config.no_item_playlist`.
- `show_barcode_not_qr(playlist_override: str | None = None)` — uses override or `self._config.barcode_not_qr_playlist`.

Each is a 3-line change reusing `_resolve_playlist`.

### 11.3 `production_main.py`: thread playlist overrides from cloud payloads

- On `openDoor`: cache `noItemPlaylist` from `product_data` on the door-sequence state object. When the recycle sensor times out and we call `show_no_item_detected`, pass that cached value as `playlist_override`.
- On `noMatch`: pass `product_data.get("noMatchPlaylist")` to `show_no_match(playlist_override=...)`.
- On `qrCode` (handled by `OptimizedBarcodeScanner.qr_code_callback`): pass `product_data.get("barcodeNotQrPlaylist")` through to `show_barcode_not_qr(playlist_override=...)`.

### 11.4 Image overlay: handle `productImage: null`

In `image_manager.py` and the overlay rendering in `main.py`, when `productImage` is `None`, empty, or fails to load: render the existing product card with text fields only (productName, productBrand, productCategory). Do not raise. This is the "first-ever scan of a new barcode" UX.

### 11.5 No `nfcUrl` reads

`production_main.py:1444` and `:1584` already prefer `qrUrl` over `nfcUrl`. Leave the fallback in place for V1 compatibility, but V2 payloads only emit `qrUrl` so the fallback path won't execute on V2 devices.

## 12. IAM

Single role `tsv6-lambda-v2-role` shared by both V2 Lambdas. Permissions:

- `firehose:PutRecord, firehose:PutRecordBatch` on `arn:aws:firehose:us-east-1:010526276861:deliverystream/tsv6-scans-v2`.
- `dynamodb:GetItem` on `master_products`, `barcode_negative_cache`, `brand_playlists`.
- `dynamodb:PutItem` on `master_products`, `barcode_negative_cache` (UpdatedBarcodeToGoUPCV2 only — split roles or use a condition if you want stricter least-privilege).
- `s3:PutObject, s3:PutObjectAcl` on `arn:aws:s3:::topper-stopper-bucket/product-images-webp/*`.
- `iot:Publish` on `arn:aws:iot:us-east-1:010526276861:topic/*/openDoor`, `*/noMatch`, `*/qrCode`, `*/error`.
- `lambda:InvokeFunction` on `UpdatedBarcodeToGoUPCV2` (BarcodeRepoLookupV2 only).
- CloudWatch Logs basic execution role.

## 13. Cost Model (10k scans/day baseline)

| Item                                               | Monthly cost |
|----------------------------------------------------|--------------|
| Firehose ingestion (~60 MB/mo at 200 B/scan JSON)  | ~$0.002      |
| Firehose Parquet conversion                        | ~$0.05       |
| S3 storage Parquet (~5 MB/mo, compounding)         | ~$0.001      |
| S3 storage WebP product images (10k unique × 30KB) | ~$0.007      |
| DynamoDB on-demand reads (300k GetItems/mo)        | ~$0.075      |
| DynamoDB on-demand writes (cold-path only ~100/mo) | ~$0.0001     |
| Lambda invocations (300k/mo, 256 MB, ~50 ms avg)   | ~$0.13       |
| Athena (Zoho 60s polling, ~4 GB scanned/mo)        | ~$0.02       |
| **Total V2 backend**                               | **~$0.30/mo**|

Cost climbs roughly linearly with scan volume. At 100k scans/day: ~$3/mo.

## 14. Testing & Validation

### 14.1 Local Lambda unit tests

For both Lambdas, mock `boto3` (`moto` or hand-rolled), test:
- Master hit path (with and without brand_playlists row)
- Negative cache hit (valid + expired)
- QR detection
- Cold-path UPC success → openDoor + Firehose put + master_products put + WebP upload
- Cold-path UPC nomatch → noMatch + negative cache put
- Cold-path image-conversion failure → falls back to source URL, never throws
- Validation errors → error response

### 14.2 Integration test on `TS_EFFC94AA`

The bench Pi we provisioned earlier in this session. Test sequence:

1. Confirm Firehose, Glue table, Athena view, brand_playlists exist.
2. Seed `brand_playlists` with `*default*` row only.
3. Deploy V2 Lambdas (do NOT modify V1 IoT rule yet).
4. Add new IoT rule `barcodeRepoLookupV2`.
5. Deploy device-side PR and flip `flowVersion` to `"v2"` in `TS_EFFC94AA` shadow payload.
6. Scan a known cached barcode (e.g., Red Bull `611269163452`):
   - This row exists in `master_products` from V1 with `productImage` = JPEG URL and no `productImageWebp` field. V2 master-hit read path falls back to `productImage`, so V2 emits the JPEG URL as `productImage` in the openDoor payload. Device renders the JPEG. This is expected behavior for V1-origin rows pre-backfill.
   - To exercise the WebP path, use a barcode the system has never seen before (step 7).
7. Scan a never-seen-before barcode to trigger UpdatedBarcodeToGoUPCV2:
   - Expect: openDoor with `productImage: null`, then second scan of the same barcode yields openDoor with WebP URL.
8. Scan a QR code (URL barcode): expect qrCode topic publish with `barcodeNotQrPlaylist`.
9. Wait 5 min, query `tsv6.v_scans_v2` via Athena: expect rows for all four scans.
10. Update `TS_EFFC94AA` shadow back to V1 (`flowVersion` removed). Repeat step 6: V1 path should fire, V2 Firehose stream should not get the row.

### 14.3 Backfill (optional, not in this spec)

A one-time Lambda or local script to walk `master_products`, download/convert/upload WebP for any row whose `productImage` is JPEG/PNG, and update the row. Out of scope for V2 core but worth scheduling once V2 is verified.

## 15. Cutover Plan

1. **Build V2 resources** (DynamoDB table, Firehose stream, Glue table, Athena view, S3 prefixes, Lambda functions, IAM role, IoT rule). All side-by-side, no V1 changes yet.
2. **Seed `brand_playlists`** with the `*default*` row only.
3. **Deploy device PR** to a single device (`TS_EFFC94AA`). Verify shadow updates carry `flowVersion: "v2"`.
4. **Verify integration test** (§14.2) end-to-end.
5. **Update existing IoT rule `barcodeRepoLookup`** to add the `flowVersion <> 'v2'` exclusion (V1 path keeps working for everyone except V2 devices). Also drop the dead `sb_BarcodeRepoLookup` action while we're editing.
6. **Add brand_playlists rows** for active sponsors as marketing supplies them.
7. **Roll V2 to a second canary device** for 1 week of production observation.
8. **Fleet-wide migration** is out of scope — separate spec.

## 16. Rollback Plan

If V2 misbehaves after device cutover:

- Set `flowVersion = "v1"` (or remove the field) in the offending device's shadow update (config push). Next scan goes through V1.
- If a V2 Lambda is broken, disable the `barcodeRepoLookupV2` IoT rule. Affected devices then hit no rule (since V1 rule excludes them via `flowVersion <> 'v2'`). They will see no openDoor responses until rolled back to V1. Only matters if we have multiple V2 devices in the field; during the canary phase only `TS_EFFC94AA` is at risk.
- All V2 sinks (Firehose, WebP S3, brand_playlists, V2 Lambdas) are write-only and can be deleted without affecting V1.

## 17. Open Questions / Future Work

- **Backfill JPEG → WebP** for the existing ~thousands of `master_products` rows. Easy follow-up Lambda, not in this spec.
- **Per-product playlist overrides** beyond brand. Add `productPlaylistOverride` and `depositPlaylistOverride` columns to `master_products`; V2 prefers product-specific over brand-default. Additive change, no IoT or Firehose schema impact.
- **Static playlist names** (no_item, no_match, barcode_not_qr) currently hard-coded as constants in V2 Lambdas. If you need per-brand or per-locale variants later, promote them to `brand_playlists` columns.
- **Migrate V1 fleet** to V2 once stable. Separate spec. The `productImage` / `productImageWebp` field separation in §10 is what makes this safe: V1 Lambdas keep writing/reading `productImage` (JPEG/PNG); V2 Lambdas read `productImageWebp` with `productImage` fallback. For `master_products` rows pre-existing from V1, V2 reads the JPEG/PNG until that row is re-resolved or backfilled.
- **Pillow Lambda layer**: pin to a specific klayers ARN to avoid upstream surprises. Pin in the Terraform/CDK in the implementation plan.
- **Athena view evolution**: when columns are added to the Parquet schema, the view can be re-created without Firehose downtime — Glue partition projection handles new columns transparently if we use it. Decide between explicit `ADD PARTITION` vs partition projection in the implementation plan.
