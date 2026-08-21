#!/usr/bin/env bash
# EC2 user-data — first-boot bootstrap for crm-operations-platform-service.
# Runs once as root via cloud-init. Installs Docker+Compose, fetches the env from Secrets Manager,
# and brings the service up. Launching the instance is then the only manual action.
#
# PREREQS on the instance:
#   • an IAM instance role with secretsmanager:GetSecretValue on the secret below
#   • outbound internet (Neon + AWS + package/image pulls); inbound tcp/8080 (or an ALB)
#   • the secret already created (see deploy-shared/create-secrets.sh) as JSON or a raw .env blob
# Logs land in /var/log/cloud-init-output.log. Re-run manually with: bash /opt/crm-bootstrap.sh
set -euxo pipefail

# ─────────────── CONFIG (edit these) ───────────────
AWS_REGION="us-east-2"
SECRET_ID="crm/operations/env"           # Secrets Manager secret holding the unified env
REPO_URL="https://github.com/<org>/crm-operations-platform-service.git"   # git clone source
REPO_REF="main"                                     # branch / tag / commit
APP_DIR="/opt/crm"
ENV_FILE="/etc/crm/env"                             # must match env_file in docker-compose.yml
# ── ECR alternative: leave REPO_URL and set IMAGE to a pushed URI to skip git clone + on-box build ──
IMAGE=""                                            # e.g. <acct>.dkr.ecr.us-east-2.amazonaws.com/crm-operations:latest

export AWS_DEFAULT_REGION="$AWS_REGION"
ARCH="$(uname -m)"   # x86_64 | aarch64

# ─────────────── 1. packages: docker, compose, git, aws cli ───────────────
if command -v dnf >/dev/null 2>&1; then
  dnf install -y docker git unzip tar
elif command -v apt-get >/dev/null 2>&1; then
  apt-get update -y
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io git unzip curl ca-certificates
fi
systemctl enable --now docker

# docker compose v2 plugin (dnf's docker package usually lacks it)
if ! docker compose version >/dev/null 2>&1; then
  mkdir -p /usr/libexec/docker/cli-plugins
  curl -fsSL "https://github.com/docker/compose/releases/download/v2.29.7/docker-compose-linux-${ARCH}" \
    -o /usr/libexec/docker/cli-plugins/docker-compose
  chmod +x /usr/libexec/docker/cli-plugins/docker-compose
fi

# aws cli v2 (Amazon Linux 2023 doesn't ship it)
if ! command -v aws >/dev/null 2>&1; then
  curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${ARCH}.zip" -o /tmp/awscliv2.zip
  ( cd /tmp && unzip -q awscliv2.zip && ./aws/install )
fi

# ─────────────── 2. fetch the secret → ENV_FILE (root-only) ───────────────
mkdir -p "$(dirname "$ENV_FILE")"
SECRET="$(aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id "$SECRET_ID" \
          --query SecretString --output text)"
# accept either a JSON {"KEY":"VAL",...} secret or a raw KEY=VALUE .env blob
printf '%s' "$SECRET" | python3 -c '
import sys, json
s = sys.stdin.read()
try:
    d = json.loads(s)
    if isinstance(d, dict):
        sys.stdout.write("".join(f"{k}={v}\n" for k, v in d.items())); raise SystemExit
except (ValueError, TypeError):
    pass
sys.stdout.write(s if s.endswith("\n") else s + "\n")
' > "$ENV_FILE"
chmod 600 "$ENV_FILE"

# ─────────────── 3. get the app (git build, or ECR pull) + up ───────────────
if [ -n "$IMAGE" ]; then
  # ECR path: no git, no on-box build
  ECR_REGISTRY="${IMAGE%%/*}"
  aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY"
  docker rm -f crm-operations 2>/dev/null || true
  docker run -d --name crm-operations --restart=always -p 8080:8080 \
    --env-file "$ENV_FILE" --memory 2g --cpus 1 "$IMAGE"
else
  # git path: clone + build via the repo's docker-compose.yml (env_file already points at ENV_FILE)
  if [ -d "$APP_DIR/.git" ]; then
    git -C "$APP_DIR" fetch --depth 1 origin "$REPO_REF" && git -C "$APP_DIR" checkout -f "$REPO_REF"
  else
    git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$APP_DIR"
  fi
  cd "$APP_DIR"
  docker compose up -d --build
fi

# ─────────────── 4. self-check ───────────────
for i in $(seq 1 30); do
  if curl -fsS --max-time 4 http://localhost:8080/health >/dev/null 2>&1; then
    echo "[user-data] crm-operations healthy on :8080"; exit 0
  fi
  sleep 5
done
echo "[user-data] WARNING: /health not ready after ~150s — check: docker logs / cloud-init-output.log" >&2
