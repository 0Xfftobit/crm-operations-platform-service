# Deploy — AWS ECS Fargate

Multi-stage `uv` image → **ECR** (us-east-2, acct `537124939480`), run on **ECS Fargate**, secrets
in **Secrets Manager**, logs to **CloudWatch**, health check on `/api/v1/health`.

## Build + push

```bash
AWS_REGION=us-east-2
ACCT=537124939480
REPO=crequity-compliance-monitoring

aws ecr get-login-password --region $AWS_REGION \
  | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$AWS_REGION.amazonaws.com
aws ecr create-repository --repository-name $REPO --region $AWS_REGION 2>/dev/null || true

docker build -t $REPO:latest .
docker tag  $REPO:latest $ACCT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
docker push $ACCT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:latest
```

## Secrets (Secrets Manager)

Create a secret named `crequity-compliance-monitoring` with the keys the task definition references:
`DATABASE_URL`, `API_KEY`, `AUDIT_SOURCE_URL`, `KAFKA_BOOTSTRAP_SERVERS`, `REDIS_URL`.

Use `STORAGE_GATEWAY=postgres` to persist audit ledgers, consent, alerts, cases, and disclosures via SQLAlchemy + psycopg. `DB_AUTO_CREATE=false` in production (run `alembic upgrade head` before start); `DB_AUTO_CREATE=true` is allowed for dev/staging only.

```bash
aws secretsmanager create-secret --name crequity-compliance-monitoring --region us-east-2 \
  --secret-string '{"DATABASE_URL":"postgresql+psycopg://…","API_KEY":"…","AUDIT_SOURCE_URL":"https://…/api/v1"}'
```

## Register + deploy

```bash
aws ecs register-task-definition --region us-east-2 \
  --cli-input-json file://deploy/compliance-monitoring-task-definition.json
# then update your service to the new task-definition revision
```
