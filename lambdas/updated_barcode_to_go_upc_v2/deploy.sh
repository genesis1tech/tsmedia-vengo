#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
NAME=UpdatedBarcodeToGoUPCV2
ROLE_ARN=$(aws iam get-role --role-name tsv6-lambda-v2-role --query 'Role.Arn' --output text)
PILLOW_LAYER_ARN="arn:aws:lambda:us-east-1:770693421928:layer:Klayers-p312-Pillow:10"

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
