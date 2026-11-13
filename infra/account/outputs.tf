output "name_servers" {
  description = "Add each as an NS record named after the subdomain (e.g. `sqlgen`) at the parent domain's DNS provider."
  value       = module.domain.name_servers
}

output "domain_name" {
  value = var.domain_name
}

output "kill_switch_function" {
  value = module.kill_switch.function_name
}

output "kill_switch_trigger_topic" {
  value = module.kill_switch.trigger_topic_arn
}
