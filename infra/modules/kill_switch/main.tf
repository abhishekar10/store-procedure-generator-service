# Budget kill switch. AWS Budgets' own actions can attach a deny policy or
# stop EC2/RDS instances, but cannot stop Fargate, an ALB or CloudFront. So a
# budget notification publishes to `trigger`, which runs a Lambda that stops
# the hourly costs (see kill_switch.py) and reports to `results`, which emails
# the owner.
#
# This lives in the long-lived `account` stack, not the demo stack, so that
# `make down` never removes it.

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  account = data.aws_caller_identity.current.account_id
  region  = data.aws_region.current.region
}

resource "aws_sns_topic" "trigger" {
  name = "${var.name}-trigger"
}

data "aws_iam_policy_document" "trigger" {
  statement {
    sid       = "BudgetsPublish"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.trigger.arn]
    principals {
      type        = "Service"
      identifiers = ["budgets.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account]
    }
  }
}

resource "aws_sns_topic_policy" "trigger" {
  arn    = aws_sns_topic.trigger.arn
  policy = data.aws_iam_policy_document.trigger.json
}

resource "aws_sns_topic" "results" {
  name = "${var.name}-results"
}

resource "aws_sns_topic_subscription" "results_email" {
  topic_arn = aws_sns_topic.results.arn
  protocol  = "email"
  endpoint  = var.alert_email # AWS emails a confirmation link first
}

# --- the function

data "archive_file" "code" {
  type        = "zip"
  source_file = "${path.module}/kill_switch.py"
  output_path = "${path.module}/.build/kill_switch.zip"
}

resource "aws_cloudwatch_log_group" "this" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 30
}

data "aws_iam_policy_document" "assume_lambda" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "this" {
  name               = var.name
  assume_role_policy = data.aws_iam_policy_document.assume_lambda.json
}

data "aws_iam_policy_document" "this" {
  statement {
    sid       = "StopService"
    actions   = ["ecs:UpdateService"]
    resources = ["arn:aws:ecs:${local.region}:${local.account}:service/${var.cluster_name}/${var.service_name}"]
  }
  statement {
    sid       = "FindAlb"
    actions   = ["elasticloadbalancing:DescribeLoadBalancers"]
    resources = ["*"] # Describe* has no resource-level permissions
  }
  statement {
    sid       = "DeleteAlb"
    actions   = ["elasticloadbalancing:DeleteLoadBalancer"]
    resources = ["arn:aws:elasticloadbalancing:${local.region}:${local.account}:loadbalancer/app/${var.alb_name}/*"]
  }
  statement {
    sid       = "Report"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.results.arn]
  }
  statement {
    sid       = "Logs"
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.this.arn}:*"]
  }
}

resource "aws_iam_role_policy" "this" {
  name   = "kill-switch"
  role   = aws_iam_role.this.id
  policy = data.aws_iam_policy_document.this.json
}

resource "aws_lambda_function" "this" {
  function_name    = var.name
  role             = aws_iam_role.this.arn
  runtime          = "python3.13"
  handler          = "kill_switch.handler"
  filename         = data.archive_file.code.output_path
  source_code_hash = data.archive_file.code.output_base64sha256
  timeout          = 60

  environment {
    variables = {
      CLUSTER_NAME      = var.cluster_name
      SERVICE_NAME      = var.service_name
      ALB_NAME          = var.alb_name
      RESULTS_TOPIC_ARN = aws_sns_topic.results.arn
    }
  }

  depends_on = [aws_cloudwatch_log_group.this, aws_iam_role_policy.this]
}

resource "aws_lambda_permission" "sns" {
  statement_id  = "AllowTriggerTopic"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.trigger.arn
}

resource "aws_sns_topic_subscription" "lambda" {
  topic_arn = aws_sns_topic.trigger.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.this.arn
}
