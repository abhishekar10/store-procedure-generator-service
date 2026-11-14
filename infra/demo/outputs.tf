output "url" {
  description = "The demo: UI at /, API at /v1/..., API docs at /api/docs."
  value       = "https://${var.domain_name}"
}

output "alb_url" {
  description = "The ALB's own name. Its certificate is for the domain, so use `url` instead."
  value       = "https://${module.service.alb_dns_name}"
}

output "ecr_api_repository_url" {
  value = data.aws_ecr_repository.api.repository_url
}

output "ecr_ui_repository_url" {
  value = data.aws_ecr_repository.ui.repository_url
}

output "api_key_secret_id" {
  value = aws_secretsmanager_secret.gemini_api_key.id
}

output "cluster_name" {
  value = module.service.cluster_name
}

output "service_name" {
  value = module.service.service_name
}

output "log_group_name" {
  value = module.service.log_group_name
}
