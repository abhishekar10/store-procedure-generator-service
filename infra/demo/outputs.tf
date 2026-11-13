output "url" {
  description = "The demo: UI at /, API at /v1/..., API docs at /api/docs."
  value       = module.frontend.url
}

output "alb_url" {
  description = "The ALB. Not reachable directly: it admits only CloudFront, and only with this stack's origin header."
  value       = "http://${module.service.alb_dns_name}"
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "api_key_secret_id" {
  value = aws_secretsmanager_secret.gemini_api_key.id
}

output "ui_bucket" {
  value = module.frontend.bucket_name
}

output "cloudfront_distribution_id" {
  value = module.frontend.distribution_id
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
