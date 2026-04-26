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
