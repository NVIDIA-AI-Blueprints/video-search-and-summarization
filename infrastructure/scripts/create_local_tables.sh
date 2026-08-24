#!/usr/bin/env bash
# Create local DynamoDB tables used by docker compose.
set -euo pipefail

ENDPOINT="${DYNAMODB_ENDPOINT_URL:-http://localhost:8001}"

export AWS_ACCESS_KEY_ID="${AWS_ACCESS_KEY_ID:-local}"
export AWS_SECRET_ACCESS_KEY="${AWS_SECRET_ACCESS_KEY:-local}"
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
unset AWS_PROFILE AWS_SESSION_TOKEN 2>/dev/null || true

aws dynamodb create-table --table-name ava-videos \
  --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S AttributeName=video_id,AttributeType=S \
  --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
  --global-secondary-indexes '[{"IndexName":"gsi1","KeySchema":[{"AttributeName":"video_id","KeyType":"HASH"}],"Projection":{"ProjectionType":"ALL"}}]' \
  --billing-mode PAY_PER_REQUEST --endpoint-url "$ENDPOINT" >/dev/null && echo "ava-videos created"

for TABLE in ava-users ava-audit; do
  aws dynamodb create-table --table-name "$TABLE" \
    --attribute-definitions AttributeName=pk,AttributeType=S AttributeName=sk,AttributeType=S \
    --key-schema AttributeName=pk,KeyType=HASH AttributeName=sk,KeyType=RANGE \
    --billing-mode PAY_PER_REQUEST --endpoint-url "$ENDPOINT" >/dev/null && echo "$TABLE created"
done
