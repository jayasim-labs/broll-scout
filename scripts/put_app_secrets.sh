#!/usr/bin/env bash
# Update B-Roll Scout app-keys secret JSON in Secrets Manager.
# Usage: SECRET_ARN=arn:aws:secretsmanager:... ./scripts/put_app_secrets.sh /path/to/keys.json
set -euo pipefail
REGION="${AWS_REGION:-us-east-1}"
ARN="${SECRET_ARN:-}"
FILE="${1:-}"
if [[ -z "$ARN" || -z "$FILE" || ! -f "$FILE" ]]; then
  echo "Usage: SECRET_ARN=<AppSecretsArn from stack outputs> $0 keys.json"
  exit 1
fi
aws secretsmanager put-secret-value --region "$REGION" --secret-id "$ARN" --secret-string "file://$FILE"
echo "Secret updated."
