#!/usr/bin/env bash
# Destroys the demo stack: everything `make up` created, including the
# API-key secret. Leaves the account stack alone -- the DNS zone and
# certificate, the ECR repositories and their images, the budget and the
# kill switch.
set -euo pipefail

cd "$(dirname "$0")/../.."
export AWS_PROFILE="${AWS_PROFILE:-sp-generator}"

aws sts get-caller-identity >/dev/null 2>&1 ||
  { echo "error: no AWS session -- run: aws sso login --profile $AWS_PROFILE" >&2; exit 1; }

terraform -chdir=infra/demo init -input=false >/dev/null
# image_tag is required by the configuration but irrelevant to a destroy.
terraform -chdir=infra/demo destroy -input=false -var image_tag=destroy
