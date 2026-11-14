# The demo stack: brought up with `make up`, destroyed with `make down`.
# Everything here is disposable -- the API-key secret is deleted on destroy.
# The DNS zone, TLS certificate and image repositories live in the
# long-lived account stack, so the address stays the same and built images
# survive `make down`.

data "aws_route53_zone" "this" {
  name = var.domain_name
}

data "aws_acm_certificate" "this" {
  domain      = var.domain_name
  statuses    = ["ISSUED"]
  most_recent = true
}

module "network" {
  source = "../modules/network"
  name   = var.name
}

# The image repositories belong to the account stack (CI pushes to them
# while the demo is down); this stack only reads them.
data "aws_ecr_repository" "api" {
  name = var.name
}

data "aws_ecr_repository" "ui" {
  name = "${var.name}-ui"
}

# The secret's value is never in Terraform: `make up` writes it from .env
# with put-secret-value, so the key is not in state or in any plan.
resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "${var.name}/gemini-api-key"
  description             = "GEMINI_API_KEY for the API tasks"
  recovery_window_in_days = 0 # delete at once on destroy, so the name is free for the next make up
}

module "service" {
  source = "../modules/service"

  name               = var.name
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.public_subnet_ids
  api_image          = "${data.aws_ecr_repository.api.repository_url}:${var.image_tag}"
  ui_image           = "${data.aws_ecr_repository.ui.repository_url}:${var.image_tag}"
  repository_arns    = [data.aws_ecr_repository.api.arn, data.aws_ecr_repository.ui.arn]
  api_key_secret_arn = aws_secretsmanager_secret.gemini_api_key.arn
  certificate_arn    = data.aws_acm_certificate.this.arn
  task_cpu           = var.task_cpu
  task_memory        = var.task_memory
  desired_count      = var.desired_count

  environment = {
    LOG_LEVEL = "INFO"
    # Viewer -> ALB -> task: the ALB appends the client to X-Forwarded-For.
    TRUSTED_PROXY_HOPS = "1"
    # The UI is served from the same origin (the ALB routes / to it), so no CORS.
    CORS_ALLOW_ORIGINS = ""
  }
}

resource "aws_route53_record" "alias" {
  zone_id = data.aws_route53_zone.this.zone_id
  name    = var.domain_name
  type    = "A"

  alias {
    name                   = module.service.alb_dns_name
    zone_id                = module.service.alb_zone_id
    evaluate_target_health = true
  }
}
