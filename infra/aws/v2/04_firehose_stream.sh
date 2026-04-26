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
