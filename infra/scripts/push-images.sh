#!/usr/bin/env bash
# Builds the API and UI images and pushes them to ECR as <repo>:$TAG,
# skipping any image whose tag is already there. Used by `make up` and by the
# deploy workflow, so both build images the same way.
#
#   TAG              image tag (the git commit, 12 characters)       required
#   GEMINI_API_KEY   the API image embeds the corpus at build time   required
#   NAME             repository name prefix                          default sp-generator
#   AWS_REGION                                                       default ap-south-1
set -euo pipefail

cd "$(dirname "$0")/../.."
: "${TAG:?TAG is required}"
: "${GEMINI_API_KEY:?GEMINI_API_KEY is required (the API image build embeds the corpus)}"
export GEMINI_API_KEY
NAME="${NAME:-sp-generator}"
REGION="${AWS_REGION:-ap-south-1}"

repo_url() {
  aws ecr describe-repositories --region "$REGION" --repository-names "$1" \
    --query 'repositories[0].repositoryUri' --output text
}

# push <repository-url> <docker build args...>
push() {
  local repo="$1"
  shift
  if aws ecr describe-images --region "$REGION" --repository-name "${repo#*/}" --image-ids "imageTag=$TAG" >/dev/null 2>&1; then
    echo "${repo#*/}:$TAG already pushed"
    return
  fi
  DOCKER_BUILDKIT=1 docker build -t "$repo:$TAG" "$@"
  docker push "$repo:$TAG"
}

API_REPO="$(repo_url "$NAME")"
UI_REPO="$(repo_url "$NAME-ui")"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "${API_REPO%%/*}" >/dev/null

# The key reaches the build as a BuildKit secret: used by the ingest step,
# never stored in an image layer.
push "$API_REPO" --secret id=gemini_key,env=GEMINI_API_KEY backend/
# VITE_API_BASE_URL empty: the UI calls /v1/... on its own origin.
push "$UI_REPO" --build-arg VITE_API_BASE_URL= frontend/
