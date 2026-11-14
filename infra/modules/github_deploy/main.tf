# Lets GitHub Actions deploy without stored AWS keys.
#
# GitHub's OIDC provider issues each workflow run a short-lived token; this
# role trusts only tokens for one repository's main branch, and can only push
# to the given ECR repositories and roll the given ECS service to a new task
# definition. It cannot create or destroy infrastructure -- that stays with
# `make up` / `make down` on the owner's machine.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
  # GitHub's subject claim names the owner and repository with their
  # immutable numeric IDs (observed in CloudTrail on the first deploy):
  #   repo:<owner>@<owner_id>/<repo>@<repo_id>:ref:refs/heads/<branch>
  # Matching on the IDs means a repository later re-created under the same
  # name cannot assume this role.
  owner   = split("/", var.github_repository)[0]
  repo    = split("/", var.github_repository)[1]
  subject = "repo:${local.owner}@${var.github_owner_id}/${local.repo}@${var.github_repository_id}:ref:refs/heads/${var.branch}"
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = [local.subject]
    }
  }
}

resource "aws_iam_role" "deploy" {
  name                 = var.name
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "deploy" {
  statement {
    sid       = "EcrAuth"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # no resource-level permissions for this action
  }
  statement {
    sid = "PushImages"
    actions = [
      "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload", "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload", "ecr:PutImage", "ecr:BatchGetImage", "ecr:DescribeImages",
      "ecr:DescribeRepositories", # the scripts look up repository URLs by name
    ]
    resources = var.repository_arns
  }
  statement {
    sid       = "TaskDefinitions"
    actions   = ["ecs:DescribeTaskDefinition", "ecs:RegisterTaskDefinition"]
    resources = ["*"] # neither supports resource-level permissions
  }
  statement {
    sid       = "RollService"
    actions   = ["ecs:DescribeServices", "ecs:UpdateService"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:service/${var.cluster_name}/${var.service_name}"]
  }
  statement {
    sid       = "PassExecutionRole"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${local.account}:role/${var.execution_role_name}"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "deploy" {
  name   = "deploy"
  role   = aws_iam_role.deploy.id
  policy = data.aws_iam_policy_document.deploy.json
}
