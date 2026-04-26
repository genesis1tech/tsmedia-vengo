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
