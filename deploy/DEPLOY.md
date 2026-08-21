# Deploy — crm-operations

Unified single-image service (5 agents + gateway on `:8080`) to **ECS Fargate**, fronted by the shared
CRM ALB at **https://operations.crm.crequity.ai**. Config is injected at runtime from Secrets Manager — the image never
contains `.env`.

## Files
- `ecs-task-definition.json` — the task definition (placeholders: `<ACCOUNT_ID>`, `<REGION>`).
- `iam-trust-policy.json` — trust for the execution role (ECS tasks).
- `iam-secrets-policy.json` — read this repo's two secrets (scoped ARNs).
- `deploy.sh` — idempotent, turnkey: build → push → register → target group → listener rule → service.

## One-time shared infra (all 5 repos reuse it)
```bash
REGION=us-east-2; ACCT=<ACCOUNT_ID>
aws ecs create-cluster --region $REGION --cluster-name crm
# execution role (create once; each repo attaches its own scoped secrets policy below)
aws iam create-role --role-name crmTaskExecutionRole \
  --assume-role-policy-document file://deploy/iam-trust-policy.json
aws iam attach-role-policy --role-name crmTaskExecutionRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
# an ALB + HTTPS:443 listener (ACM cert for *.crm.crequity.ai) → note the listener ARN for deploy.sh
```

## Per-repo deploy
```bash
REGION=us-east-2
# 1) secrets (values live only here — rotate the Neon password first)
aws secretsmanager create-secret --region $REGION --name crm/operations/DATABASE_URL \
  --secret-string 'postgresql+psycopg://neondb_owner:<NEW_PW>@ep-super-sound-ay9w5vhg.c-5.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require'
aws secretsmanager create-secret --region $REGION --name crm/operations/API_KEY \
  --secret-string '<strong-production-key>'

# 2) let the execution role read THIS repo's secrets (fill <REGION>/<ACCOUNT_ID> in the json first)
aws iam put-role-policy --role-name crmTaskExecutionRole \
  --policy-name crm-operations-secrets --policy-document file://deploy/iam-secrets-policy.json

# 3) deploy (edit the CONFIG block in deploy.sh, or export the shared vars, then run)
bash deploy/deploy.sh
```

## Verify
```bash
curl -s https://operations.crm.crequity.ai/health          # {"status":"ok"} from the gateway
curl -s https://operations.crm.crequity.ai/ready           # gateway + upstreams
# a specific agent: https://operations.crm.crequity.ai/<agent-slug>/health
```

## Notes
- **Egress to Neon is required at boot** — the entrypoint's `db_bootstrap.py` creates each agent's
  schema before the app starts. Public subnet + `assignPublicIp=ENABLED`, or private subnet + NAT.
- Omit the `DATABASE_URL` secret and everything still boots — on in-memory/sqlite (data lost on restart).
- Each repo uses a distinct ALB listener-rule priority (no collisions): 10/20/30/40/50.
