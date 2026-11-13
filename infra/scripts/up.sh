#!/usr/bin/env bash
# Brings the demo stack up. CREATES BILLABLE AWS RESOURCES (about $1.25/day
# while up; see infra/README.md). Tear down with `make down`.
#
#   1. terraform apply with zero API tasks -- the one approval prompt; it
#      shows the whole plan
#   2. write GEMINI_API_KEY from .env into Secrets Manager (never into state)
#   3. build the API and UI images and push them to ECR, tagged with the git
#      commit (the UI is built for same-origin API calls)
#   4. scale the service up and wait until the ALB reports it healthy
#
# Needs the account stack's DNS zone and certificate (make dns-zone, then
# make account-apply; see infra/README.md).
set -euo pipefail

cd "$(dirname "$0")/../.."
export AWS_PROFILE="${AWS_PROFILE:-sp-generator}"
REGION="${AWS_REGION:-ap-south-1}"
TAG="${IMAGE_TAG:-$(git rev-parse --short=12 HEAD)}"
COUNT="${DESIRED_COUNT:-1}"
TF="terraform -chdir=infra/demo"

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

# push <repository-url> <docker build args...>: build and push unless the tag exists.
push() {
  local repo="$1"
  shift
  if aws ecr describe-images --repository-name "${repo#*/}" --image-ids "imageTag=$TAG" >/dev/null 2>&1; then
    echo "${repo#*/}:$TAG already pushed"
    return
  fi
  DOCKER_BUILDKIT=1 docker build -t "$repo:$TAG" "$@"
  docker push "$repo:$TAG"
}

step "images $TAG -> ECR"
API_REPO="$(out ecr_api_repository_url)"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${API_REPO%%/*}" >/dev/null
push "$API_REPO" --secret id=gemini_key,env=GEMINI_API_KEY backend/
# VITE_API_BASE_URL empty: the UI calls /v1/... on its own origin.
push "$(out ecr_ui_repository_url)" --build-arg VITE_API_BASE_URL= frontend/

step "scale to $COUNT task(s) and wait for healthy"
$TF apply -input=false -auto-approve -var "image_tag=$TAG" -var "desired_count=$COUNT"

step "up"
echo "UI and API: $(out url)    (API docs at /api/docs)"
echo "Logs: aws logs tail $(out log_group_name) --follow --profile $AWS_PROFILE"
echo "Costs money until: make down"
