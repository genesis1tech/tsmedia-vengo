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
