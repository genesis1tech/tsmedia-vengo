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
