#!/usr/bin/env bash
# Rolls the running demo service to the images tagged $TAG: registers a copy
# of its current task definition with the two images swapped, points the
# service at it, and waits until the new task is healthy (the ECS deployment
# circuit breaker rolls back if it never is).
#
# The demo stack exists only between `make up` and `make down`. When it is
# down this does nothing and says so -- the images are already in ECR, and
# the next `make up` deploys them.
#
#   TAG          image tag to deploy                       required
#   NAME         cluster/repository name prefix            default sp-generator
#   AWS_REGION                                             default ap-south-1
set -euo pipefail

: "${TAG:?TAG is required}"
NAME="${NAME:-sp-generator}"
REGION="${AWS_REGION:-ap-south-1}"
CLUSTER="$NAME"
SERVICE="$NAME-api"

current="$(aws ecs describe-services --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[?status==`ACTIVE`].taskDefinition | [0]' --output text 2>/dev/null || true)"
if [ -z "$current" ] || [ "$current" = "None" ]; then
  echo "::notice::The demo stack is down, so there is nothing to roll. Images for $TAG are in ECR; the next 'make up' deploys them."
  exit 0
fi

repo_url() {
  aws ecr describe-repositories --region "$REGION" --repository-names "$1" \
    --query 'repositories[0].repositoryUri' --output text
}
api_image="$(repo_url "$NAME"):$TAG"
ui_image="$(repo_url "$NAME-ui"):$TAG"

# Keep only the fields register-task-definition accepts; swap the images.
definition="$(aws ecs describe-task-definition --region "$REGION" --task-definition "$current" \
  --query taskDefinition --output json |
  jq --arg api "$api_image" --arg ui "$ui_image" '
    .containerDefinitions |= map(
      if .name == "api" then .image = $api
      elif .name == "ui" then .image = $ui
      else . end)
    | {family, executionRoleArn, networkMode, containerDefinitions,
       requiresCompatibilities, cpu, memory, runtimePlatform}')"

new="$(aws ecs register-task-definition --region "$REGION" --cli-input-json "$definition" \
  --query 'taskDefinition.taskDefinitionArn' --output text)"
echo "Rolling $CLUSTER/$SERVICE from ${current##*/} to ${new##*/}"
aws ecs update-service --region "$REGION" --cluster "$CLUSTER" --service "$SERVICE" --task-definition "$new" >/dev/null
aws ecs wait services-stable --region "$REGION" --cluster "$CLUSTER" --services "$SERVICE"
echo "Deployed $TAG"
