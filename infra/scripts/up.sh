#!/usr/bin/env bash
# Brings the demo stack up. CREATES BILLABLE AWS RESOURCES (about $1.25/day
# while up; see infra/README.md). Tear down with `make down`.
#
#   1. terraform apply with zero API tasks -- the one approval prompt; it
#      shows the whole plan
#   2. write GEMINI_API_KEY from .env into Secrets Manager (never into state)
#   3. build the API image and push it to ECR, tagged with the git commit
#   4. build the UI for same-origin API calls and upload it to S3
#   5. scale the service up and wait until the ALB reports it healthy
set -euo pipefail

cd "$(dirname "$0")/../.."
export AWS_PROFILE="${AWS_PROFILE:-sp-generator}"
REGION="${AWS_REGION:-ap-south-1}"
TAG="${IMAGE_TAG:-$(git rev-parse --short=12 HEAD)}"
COUNT="${DESIRED_COUNT:-1}"
TF="terraform -chdir=infra/demo"
NODE_IMAGE="node:22.22.1-slim" # the version frontend/Dockerfile builds with
UI_DIST="infra/.build/ui"      # gitignored; vite empties it on each build

step() { printf '\n== %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

aws sts get-caller-identity >/dev/null 2>&1 ||
  die "no AWS session for profile $AWS_PROFILE -- run: aws sso login --profile $AWS_PROFILE"
GEMINI_API_KEY="$(grep -E '^GEMINI_API_KEY=' .env 2>/dev/null | cut -d= -f2- || true)"
[ -n "$GEMINI_API_KEY" ] || die "GEMINI_API_KEY is not set in .env"
export GEMINI_API_KEY
if [ -n "$(git status --porcelain)" ]; then
  echo "warning: uncommitted changes -- the image is tagged $TAG but includes them"
fi

step "terraform apply (service at 0 tasks)"
$TF init -input=false >/dev/null
$TF apply -input=false -var "image_tag=$TAG" -var "desired_count=0"
out() { $TF output -raw "$1"; }

step "API key -> Secrets Manager"
printf '%s' "$GEMINI_API_KEY" |
  aws secretsmanager put-secret-value --secret-id "$(out api_key_secret_id)" --secret-string file:///dev/stdin >/dev/null

step "API image $TAG -> ECR"
REPO="$(out ecr_repository_url)"
if aws ecr describe-images --repository-name "${REPO#*/}" --image-ids "imageTag=$TAG" >/dev/null 2>&1; then
  echo "already pushed"
else
  aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
  DOCKER_BUILDKIT=1 docker build --secret id=gemini_key,env=GEMINI_API_KEY -t "$REPO:$TAG" backend/
  docker push "$REPO:$TAG"
fi

step "UI -> S3"
mkdir -p "$UI_DIST"
# VITE_API_BASE_URL empty: the UI calls /v1/... on its own (CloudFront) origin.
docker run --rm -u "$(id -u):$(id -g)" -e HOME=/tmp -e VITE_API_BASE_URL= \
  -v "$PWD/frontend:/src:ro" -v "$PWD/$UI_DIST:/out" "$NODE_IMAGE" \
  sh -c 'cp -r /src /tmp/w && cd /tmp/w && npm ci --silent && npx vite build --outDir /out --emptyOutDir'
aws s3 sync "$UI_DIST" "s3://$(out ui_bucket)" --delete
aws cloudfront create-invalidation --distribution-id "$(out cloudfront_distribution_id)" --paths '/*' >/dev/null

step "scale to $COUNT task(s) and wait for healthy"
$TF apply -input=false -auto-approve -var "image_tag=$TAG" -var "desired_count=$COUNT"

step "up"
echo "UI and API: $(out url)    (API docs at /api/docs)"
echo "Logs: aws logs tail $(out log_group_name) --follow --profile $AWS_PROFILE"
echo "Costs money until: make down"
