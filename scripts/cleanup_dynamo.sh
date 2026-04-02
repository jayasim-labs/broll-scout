#!/bin/bash
set -euo pipefail

# Cleanup script for B-Roll Scout DynamoDB tables.
#
# Usage:
#   ./scripts/cleanup_dynamo.sh              # Wipe jobs, segments, results, projects, usage (keep caches)
#   ./scripts/cleanup_dynamo.sh --all        # Wipe everything including transcript & channel caches
#   ./scripts/cleanup_dynamo.sh --job <id>   # Delete a single job and its segments/results

REGION="us-east-1"
PREFIX="broll_"
TABLES_JOB=("jobs" "segments" "results" "projects" "usage")
TABLES_CACHE=("transcripts" "feedback" "channel_cache")
TABLES_SETTINGS=("settings")

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

delete_all_items() {
  local table="$1"
  local keys="$2"  # comma-separated key names e.g. "job_id" or "job_id,segment_id"

  local count
  count=$(aws dynamodb scan --table-name "${PREFIX}${table}" --region "$REGION" --select COUNT --output json | python3 -c "import json,sys; print(json.load(sys.stdin)['Count'])")

  if [ "$count" -eq 0 ]; then
    echo -e "  ${GREEN}${PREFIX}${table}: already empty${NC}"
    return
  fi

  echo -e "  ${YELLOW}${PREFIX}${table}: deleting $count items...${NC}"

  IFS=',' read -ra KEY_ARRAY <<< "$keys"
  local proj
  proj=$(IFS=','; echo "${KEY_ARRAY[*]}")

  aws dynamodb scan --table-name "${PREFIX}${table}" --region "$REGION" \
    --projection-expression "$proj" --output json | \
  python3 -c "
import json, sys, subprocess
data = json.load(sys.stdin)
keys = '$keys'.split(',')
for item in data.get('Items', []):
    key_dict = {}
    for k in keys:
        key_dict[k] = item[k]
    subprocess.run([
        'aws', 'dynamodb', 'delete-item',
        '--table-name', '${PREFIX}${table}',
        '--key', json.dumps(key_dict),
        '--region', '$REGION'
    ], check=True, capture_output=True)
print(f'  Deleted {len(data.get(\"Items\", []))} items from ${PREFIX}${table}')
"
}

delete_job() {
  local job_id="$1"
  echo -e "${YELLOW}Deleting job $job_id...${NC}"

  aws dynamodb delete-item --table-name "${PREFIX}jobs" \
    --key "{\"job_id\": {\"S\": \"$job_id\"}}" --region "$REGION" 2>/dev/null || true

  # Segments for this job
  aws dynamodb query --table-name "${PREFIX}segments" --region "$REGION" \
    --key-condition-expression "job_id = :jid" \
    --expression-attribute-values "{\":jid\": {\"S\": \"$job_id\"}}" \
    --projection-expression "job_id, segment_id" --output json | \
  python3 -c "
import json, sys, subprocess
data = json.load(sys.stdin)
for item in data.get('Items', []):
    subprocess.run([
        'aws', 'dynamodb', 'delete-item',
        '--table-name', '${PREFIX}segments',
        '--key', json.dumps({'job_id': item['job_id'], 'segment_id': item['segment_id']}),
        '--region', '$REGION'
    ], check=True, capture_output=True)
print(f'  Deleted {len(data.get(\"Items\", []))} segments')
"

  # Results for this job
  aws dynamodb query --table-name "${PREFIX}results" --region "$REGION" \
    --key-condition-expression "job_id = :jid" \
    --expression-attribute-values "{\":jid\": {\"S\": \"$job_id\"}}" \
    --projection-expression "job_id, result_id" --output json | \
  python3 -c "
import json, sys, subprocess
data = json.load(sys.stdin)
for item in data.get('Items', []):
    subprocess.run([
        'aws', 'dynamodb', 'delete-item',
        '--table-name', '${PREFIX}results',
        '--key', json.dumps({'job_id': item['job_id'], 'result_id': item['result_id']}),
        '--region', '$REGION'
    ], check=True, capture_output=True)
print(f'  Deleted {len(data.get(\"Items\", []))} results')
"
  echo -e "${GREEN}Job $job_id cleaned up${NC}"
}

show_status() {
  echo "Current DynamoDB item counts:"
  for table in "${TABLES_JOB[@]}" "${TABLES_CACHE[@]}" "${TABLES_SETTINGS[@]}"; do
    count=$(aws dynamodb scan --table-name "${PREFIX}${table}" --region "$REGION" --select COUNT --output json 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin)['Count'])" 2>/dev/null || echo "?")
    echo "  ${PREFIX}${table}: $count items"
  done
}

# --- Main ---

if [ "${1:-}" = "--job" ] && [ -n "${2:-}" ]; then
  delete_job "$2"
  exit 0
fi

echo -e "${RED}=== B-Roll Scout DynamoDB Cleanup ===${NC}"
show_status
echo ""

if [ "${1:-}" = "--all" ]; then
  echo -e "${RED}Wiping ALL tables (including transcript & channel caches)...${NC}"
  delete_all_items "jobs" "job_id"
  delete_all_items "segments" "job_id,segment_id"
  delete_all_items "results" "job_id,result_id"
  delete_all_items "projects" "project_id"
  delete_all_items "usage" "period"
  delete_all_items "transcripts" "video_id"
  delete_all_items "feedback" "result_id"
  delete_all_items "channel_cache" "channel_id"
else
  echo "Wiping jobs, projects & usage (keeping transcripts, channels, settings)..."
  delete_all_items "jobs" "job_id"
  delete_all_items "segments" "job_id,segment_id"
  delete_all_items "results" "job_id,result_id"
  delete_all_items "projects" "project_id"
  delete_all_items "usage" "period"
fi

echo ""
echo -e "${GREEN}=== Cleanup complete ===${NC}"
show_status
