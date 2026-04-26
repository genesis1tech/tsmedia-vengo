# V2 brand playlist routing (`brand_playlists` DDB table)

The V2 cloud path resolves per-brand PiSignage playlists from the
`brand_playlists` DynamoDB table. Each row maps a `productBrand`
(case-sensitive, exact match against the value stored in
`master_products.productBrand`) to the playlists the device should switch
to during a transaction.

If no row matches a scanned product's brand, the V2 Lambda falls back to
the `*default*` row, which is seeded by `infra/aws/v2/01_brand_playlists.sh`
to the four built-in TSV6 playlists.

## Schema

| Attribute            | Required | Notes                                                                  |
|----------------------|----------|------------------------------------------------------------------------|
| `brand` (HASH key)   | yes      | The exact `productBrand` string, or `*default*` for the fallback row.  |
| `depositPlaylist`    | yes      | Playlist shown while the door is open and the user is depositing.      |
| `productPlaylist`    | yes      | Playlist shown after a successful deposit (product card + QR).         |
| `noItemPlaylist`     | no       | Playlist shown when the recycle sensor never sees an item drop.        |
| `noMatchPlaylist`    | no       | Playlist shown when the barcode resolves to nothing in any source.     |
| `barcodeNotQrPlaylist`| no      | Playlist shown when the user scanned a URL/QR instead of a UPC.        |

The five `*Playlist` fields all expect PiSignage playlist names (not IDs).

## Adding a sponsor brand

The brand string must match `master_products.productBrand` exactly. To
look up what's already there for a known UPC:

```bash
aws dynamodb get-item --table-name master_products \
  --key '{"barcode":{"S":"611269163452"}}' \
  --query 'Item.productBrand'
```

Then add or replace the brand row:

```bash
aws dynamodb put-item --table-name brand_playlists --item '{
  "brand":                {"S": "Red Bull"},
  "depositPlaylist":      {"S": "tsv6_redbull_deposit"},
  "productPlaylist":      {"S": "tsv6_redbull_product"},
  "noItemPlaylist":       {"S": "tsv6_redbull_no_item"},
  "noMatchPlaylist":      {"S": "tsv6_redbull_no_match"},
  "barcodeNotQrPlaylist": {"S": "tsv6_redbull_barcode_not_qr"}
}'
```

Omit any optional `*Playlist` attribute to inherit the corresponding value
from the `*default*` row.

## Verifying

After adding a row, scan a product of that brand from a V2 device and
inspect either:

- The MQTT `openDoor` payload published to `<thing>/openDoor` (the
  `depositPlaylist` / `productPlaylist` fields will reflect the new
  values), or
- Athena: `SELECT depositplaylist, productplaylist, productbrand FROM
  tsv6.v_scans_v2 WHERE productbrand = 'Red Bull' ORDER BY scantimestamp
  DESC LIMIT 5;`

## Default row contents

```bash
aws dynamodb get-item --table-name brand_playlists \
  --key '{"brand":{"S":"*default*"}}'
```

Currently:

| Attribute        | Value                  |
|------------------|------------------------|
| `depositPlaylist`| `tsv6_processing`      |
| `productPlaylist`| `tsv6_product_display` |

(The default row was seeded with only the two required fields; the
device falls back to its hard-coded constants — `tsv6_no_item_detected`,
`tsv6_no_match`, `tsv6_barcode_not_qr` — when `noItemPlaylist`,
`noMatchPlaylist`, or `barcodeNotQrPlaylist` are not in either the
brand row or the default row.)

## Removing a brand override

```bash
aws dynamodb delete-item --table-name brand_playlists \
  --key '{"brand":{"S":"Red Bull"}}'
```

The next scan for that brand will fall back to `*default*`.
