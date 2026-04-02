#!/usr/bin/env bash
# Build and deploy B-Roll Scout to AWS (SAM). Requires: aws CLI, sam CLI, configured credentials.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

REGION="${AWS_REGION:-us-east-1}"
STACK="${STACK_NAME:-broll-scout}"

if ! command -v sam &>/dev/null; then
  echo "Install AWS SAM CLI: brew install aws-sam-cli"
  exit 1
fi

echo "AWS identity:"
aws sts get-caller-identity --region "$REGION"
echo ""
echo "Building..."
sam build
echo ""
echo "Deploying stack '$STACK' to $REGION..."
sam deploy \
  --stack-name "$STACK" \
  --region "$REGION" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-confirm-changeset \
  "$@"

echo ""
echo "Stack outputs:"
aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs" --output table
