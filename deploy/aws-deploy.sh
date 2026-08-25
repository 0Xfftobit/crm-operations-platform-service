#!/usr/bin/env bash
# Direct AWS deployment — no GitHub Actions.
#
#   bash deploy/aws-deploy.sh                 # build HEAD, push to ECR, roll the ECS service
#   SKIP_BUILD=1 IMAGE_TAG=<tag> bash ...     # redeploy an image already in ECR
#   NO_WAIT=1 bash ...                        # don't block on service stability
#
# The current task definition is fetched from ECS and only the image is
# swapped, so runtime config (env, secrets, resources) always follows what
# is live rather than a checked-in template.
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:-charles}"; export AWS_PROFILE
REGION="${REGION:-us-east-2}"
CLUSTER="${CLUSTER:-cr-dev}"
SERVICE="crequity-crm-operations-platform-service-service"
ECR_REPO="crequity-crm-operations-platform-service"
ACCOUNT_ID="${ACCOUNT_ID:-537124939480}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"
ECR="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
TAG="${IMAGE_TAG:-$(git rev-parse HEAD)}"
IMAGE="${ECR}/${ECR_REPO}:${TAG}"

echo "==> [$SERVICE] deploy $IMAGE"
if [ "${SKIP_BUILD:-0}" != "1" ]; then
  docker build -t "$IMAGE" .
  aws ecr get-login-password --region "$REGION" \
    | docker login --username AWS --password-stdin "$ECR" >/dev/null
  docker push "$IMAGE"
fi

TD_ARN=$(aws ecs describe-services --cluster "$CLUSTER" --region "$REGION" \
  --services "$SERVICE" --query 'services[0].taskDefinition' --output text)
_TDC="$(mktemp)"; _TDN="$(mktemp)"
aws ecs describe-task-definition --region "$REGION" --task-definition "$TD_ARN" \
  --query 'taskDefinition' --output json > "$_TDC"
python3 - "$IMAGE" "$_TDC" "$_TDN" <<'PYEOF'
import json, sys
image, tdc, tdn = sys.argv[1], sys.argv[2], sys.argv[3]
td = json.load(open(tdc))
for k in ("taskDefinitionArn", "revision", "status", "requiresAttributes",
          "compatibilities", "registeredAt", "registeredBy"):
    td.pop(k, None)
td["containerDefinitions"][0]["image"] = image
json.dump(td, open(tdn, "w"))
PYEOF
NEW_TD=$(aws ecs register-task-definition --region "$REGION" \
  --cli-input-json "file://$_TDN" \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "==> registered ${NEW_TD##*/}"

aws ecs update-service --cluster "$CLUSTER" --region "$REGION" \
  --service "$SERVICE" --task-definition "$NEW_TD" --force-new-deployment \
  --query 'service.serviceName' --output text >/dev/null
echo "==> service rolling"
if [ "${NO_WAIT:-0}" != "1" ]; then
  aws ecs wait services-stable --cluster "$CLUSTER" --region "$REGION" --services "$SERVICE"
  echo "==> [$SERVICE] stable on ${NEW_TD##*/}"
fi
