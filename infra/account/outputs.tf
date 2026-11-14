output "name_servers" {
  description = "Add each as an NS record named after the subdomain (e.g. `sqlgen`) at the parent domain's DNS provider."
  value       = module.domain.name_servers
}

output "domain_name" {
  value = var.domain_name
}

output "github_deploy_role_arn" {
  description = "Set as the AWS_DEPLOY_ROLE_ARN repository variable in GitHub."
  value       = module.github_deploy.role_arn
}

output "ecr_api_repository_url" {
  value = module.ecr_api.repository_url
}

output "kill_switch_function" {
  value = module.kill_switch.function_name
}

output "kill_switch_trigger_topic" {
  value = module.kill_switch.trigger_topic_arn
}
