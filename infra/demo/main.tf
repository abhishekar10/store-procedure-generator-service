# The demo stack: brought up with `make up`, destroyed with `make down`.
# Everything here is disposable -- the ECR repository, the UI bucket and the
# API-key secret are all deleted on destroy.

module "network" {
  source = "../modules/network"
  name   = var.name
}

module "ecr" {
  source       = "../modules/ecr"
  name         = var.name
  force_delete = true
}

# The secret's value is never in Terraform: `make up` writes it from .env
# with put-secret-value, so the key is not in state or in any plan.
resource "aws_secretsmanager_secret" "gemini_api_key" {
  name                    = "${var.name}/gemini-api-key"
  description             = "GEMINI_API_KEY for the API tasks"
  recovery_window_in_days = 0 # delete at once on destroy, so the name is free for the next make up
}

resource "random_password" "origin_verify" {
  length  = 32
  special = false
}

module "service" {
  source = "../modules/service"

  name               = var.name
  vpc_id             = module.network.vpc_id
  subnet_ids         = module.network.public_subnet_ids
  image              = "${module.ecr.repository_url}:${var.image_tag}"
  repository_arn     = module.ecr.repository_arn
  api_key_secret_arn = aws_secretsmanager_secret.gemini_api_key.arn
  task_cpu           = var.task_cpu
  task_memory        = var.task_memory
  desired_count      = var.desired_count

  origin_verify_header_value = random_password.origin_verify.result

  environment = {
    LOG_LEVEL = "INFO"
    # Viewer -> CloudFront -> ALB -> task: two proxies append to X-Forwarded-For.
    TRUSTED_PROXY_HOPS = "2"
    # The UI is served from the same CloudFront origin, so no CORS origins.
    CORS_ALLOW_ORIGINS = ""
  }
}

module "frontend" {
  source = "../modules/frontend"

  name                       = var.name
  api_origin_domain          = module.service.alb_dns_name
  origin_verify_header_value = random_password.origin_verify.result
  force_destroy              = true
}
