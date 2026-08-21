#!/usr/bin/env bash
# Turnkey deploy of THIS repo's unified image to ECS Fargate behind the shared CRM ALB.
# Idempotent: safe to re-run — it rolls the service on repeat runs.
#
# One-time shared infra (all 5 repos reuse it): an ECS cluster, an ALB with an HTTPS listener,
# the crmTaskExecutionRole, and DNS for the gateway domains. See deploy/DEPLOY.md.
#
# Usage: fill the CONFIG block (or export the vars), authenticate the AWS CLI, then:
#   bash deploy/deploy.sh
set -euo pipefail

# ── shared infra (fill once; identical across all 5 repos) ─────────────────────────
AWS_REGION="${AWS_REGION:-us-east-2}"
ACCOUNT_ID="${ACCOUNT_ID:-<ACCOUNT_ID>}"
CLUSTER="${CLUSTER:-crm}"
VPC_ID="${VPC_ID:-<VPC_ID>}"
SUBNETS="${SUBNETS:-<subnet-a>,<subnet-b>}"        # MUST have egress to Neon (public+assignPublicIp, or private+NAT)
SECURITY_GROUPS="${SECURITY_GROUPS:-<sg-app>}"     # inbound tcp/8080 from the ALB security group
ALB_LISTENER_ARN="${ALB_LISTENER_ARN:-<https-listener-arn>}"
EXEC_ROLE_ARN="${EXEC_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/crmTaskExecutionRole}"
TASK_ROLE_ARN="${TASK_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/crmTaskRole}"
ASSIGN_PUBLIC_IP="${ASSIGN_PUBLIC_IP:-ENABLED}"

# ── per-repo (prefilled for THIS repo) ─────────────────────────────────────────────
FAMILY="crm-operations"
SLUG="operations"
DOMAIN="operations.crm.crequity.ai"
RULE_PRIORITY="40"
GRACE="120"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DESIRED_COUNT="${DESIRED_COUNT:-1}"

command -v jq >/dev/null || { echo "!! jq is required"; exit 1; }
command -v docker >/dev/null || { echo "!! docker is required"; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # repo root
ECR="${ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"
IMAGE="${ECR}/${FAMILY}:${IMAGE_TAG}"
echo "==> [$FAMILY] region=$AWS_REGION cluster=$CLUSTER domain=$DOMAIN"

# 1) secrets must already exist (values are NEVER stored in this repo)
for s in DATABASE_URL API_KEY; do
  aws secretsmanager describe-secret --region "$AWS_REGION" --secret-id "crm/$SLUG/$s" >/dev/null 2>&1 || {
    echo "!! missing secret crm/$SLUG/$s — create it once, then re-run:"
    echo "   aws secretsmanager create-secret --region $AWS_REGION --name crm/$SLUG/$s --secret-string '<value>'"
    exit 1; }
done
DB_ARN=$(aws secretsmanager describe-secret --region "$AWS_REGION" --secret-id "crm/$SLUG/DATABASE_URL" --query ARN --output text)
KEY_ARN=$(aws secretsmanager describe-secret --region "$AWS_REGION" --secret-id "crm/$SLUG/API_KEY" --query ARN --output text)

# 2) ECR repo + build + push
aws ecr describe-repositories --region "$AWS_REGION" --repository-names "$FAMILY" >/dev/null 2>&1 \
  || aws ecr create-repository --region "$AWS_REGION" --repository-name "$FAMILY" >/dev/null
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR"
docker build -t "$IMAGE" "$HERE"
docker push "$IMAGE"

# 3) render + register the task definition (inject the real image + resolved secret ARNs + roles)
RENDERED="$(mktemp)"
sed -e "s#<ACCOUNT_ID>#${ACCOUNT_ID}#g" -e "s#<REGION>#${AWS_REGION}#g" "$HERE/deploy/ecs-task-definition.json" \
 | jq --arg img "$IMAGE" --arg db "$DB_ARN" --arg key "$KEY_ARN" --arg er "$EXEC_ROLE_ARN" --arg tr "$TASK_ROLE_ARN" '
     .executionRoleArn=$er | .taskRoleArn=$tr
     | .containerDefinitions[0].image=$img
     | .containerDefinitions[0].secrets |= map(
         if .name=="DATABASE_URL" then .valueFrom=$db
         elif .name=="API_KEY" then .valueFrom=$key else . end)
   ' > "$RENDERED"
aws ecs register-task-definition --region "$AWS_REGION" --cli-input-json "file://$RENDERED" \
  --query 'taskDefinition.taskDefinitionArn' --output text | sed 's/^/==> registered /'
rm -f "$RENDERED"

# 4) target group (idempotent), HTTP:8080, health check /health
TG_ARN=$(aws elbv2 describe-target-groups --region "$AWS_REGION" --names "${FAMILY}-tg" \
           --query 'TargetGroups[0].TargetGroupArn' --output text 2>/dev/null || true)
if [ -z "${TG_ARN:-}" ] || [ "$TG_ARN" = "None" ]; then
  TG_ARN=$(aws elbv2 create-target-group --region "$AWS_REGION" --name "${FAMILY}-tg" \
    --protocol HTTP --port 8080 --vpc-id "$VPC_ID" --target-type ip \
    --health-check-protocol HTTP --health-check-path /health --health-check-interval-seconds 30 \
    --healthy-threshold-count 2 --unhealthy-threshold-count 3 --matcher HttpCode=200 \
    --query 'TargetGroups[0].TargetGroupArn' --output text)
fi
echo "==> target group $TG_ARN"

# 5) listener rule: host-header DOMAIN -> this target group (idempotent by priority)
aws elbv2 describe-rules --region "$AWS_REGION" --listener-arn "$ALB_LISTENER_ARN" \
    --query "Rules[?Priority=='${RULE_PRIORITY}'].RuleArn" --output text 2>/dev/null | grep -q arn \
  || aws elbv2 create-rule --region "$AWS_REGION" --listener-arn "$ALB_LISTENER_ARN" --priority "$RULE_PRIORITY" \
       --conditions Field=host-header,Values="$DOMAIN" \
       --actions Type=forward,TargetGroupArn="$TG_ARN" >/dev/null
echo "==> listener rule host=$DOMAIN prio=$RULE_PRIORITY -> ${FAMILY}-tg"

# 6) service: create first time, else roll a new deployment
if aws ecs describe-services --region "$AWS_REGION" --cluster "$CLUSTER" --services "$FAMILY" \
     --query 'services[0].status' --output text 2>/dev/null | grep -q ACTIVE; then
  aws ecs update-service --region "$AWS_REGION" --cluster "$CLUSTER" --service "$FAMILY" \
    --task-definition "$FAMILY" --force-new-deployment >/dev/null
  echo "==> rolled service $FAMILY"
else
  aws ecs create-service --region "$AWS_REGION" --cluster "$CLUSTER" --service-name "$FAMILY" \
    --task-definition "$FAMILY" --desired-count "$DESIRED_COUNT" --launch-type FARGATE \
    --load-balancers "targetGroupArn=${TG_ARN},containerName=${FAMILY},containerPort=8080" \
    --health-check-grace-period-seconds "$GRACE" \
    --network-configuration "awsvpcConfiguration={subnets=[${SUBNETS}],securityGroups=[${SECURITY_GROUPS}],assignPublicIp=${ASSIGN_PUBLIC_IP}}" >/dev/null
  echo "==> created service $FAMILY"
fi
echo "==> done → https://${DOMAIN}/health   (every agent via https://${DOMAIN}/<slug>/*)"
