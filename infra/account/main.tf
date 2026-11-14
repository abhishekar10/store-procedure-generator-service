# Long-lived account guard rails: the monthly budget and the kill switch.
#
# The budget was first created by hand (aws budgets create-budget) and is
# adopted here by the import block below, so applying this stack changes it
# in place -- it adds the kill-switch topic to the top threshold -- rather
# than creating a second one.

data "aws_caller_identity" "current" {}

import {
  to = aws_budgets_budget.monthly
  id = "${data.aws_caller_identity.current.account_id}:monthly-1000-inr"
}

module "domain" {
  source      = "../modules/domain"
  domain_name = var.domain_name
}

# Image repositories live here, not in the demo stack, so CI can push images
# while the demo is down and `make up` reuses them.
module "ecr_api" {
  source      = "../modules/ecr"
  name        = var.demo_name
  keep_images = 3
}

module "ecr_ui" {
  source      = "../modules/ecr"
  name        = "${var.demo_name}-ui"
  keep_images = 3
}

module "github_deploy" {
  source = "../modules/github_deploy"

  name                = "${var.demo_name}-github-deploy"
  github_repository   = var.github_repository
  repository_arns     = [module.ecr_api.repository_arn, module.ecr_ui.repository_arn]
  cluster_name        = var.demo_name
  service_name        = "${var.demo_name}-api"
  execution_role_name = "${var.demo_name}-execution"
}

module "kill_switch" {
  source = "../modules/kill_switch"

  name         = "${var.demo_name}-kill-switch"
  cluster_name = var.demo_name
  service_name = "${var.demo_name}-api"
  alb_name     = var.demo_name
  alert_email  = var.alert_email
}

resource "aws_budgets_budget" "monthly" {
  name         = "monthly-1000-inr"
  budget_type  = "COST"
  limit_amount = var.monthly_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  dynamic "notification" {
    for_each = var.alert_thresholds
    content {
      notification_type          = "ACTUAL"
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      subscriber_email_addresses = [var.alert_email]
    }
  }

  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = var.kill_switch_threshold
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.alert_email]
    subscriber_sns_topic_arns  = [module.kill_switch.trigger_topic_arn]
  }
}
