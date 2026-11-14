variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "alert_email" {
  description = "Budget alerts and kill-switch reports. Set in terraform.tfvars (gitignored) or TF_VAR_alert_email."
  type        = string
}

variable "monthly_limit_usd" {
  description = "About INR 1,000. The Budgets API accepts only USD for this account."
  type        = string
  default     = "11.5"
}

variable "alert_thresholds" {
  description = "Percent of the limit, on actual spend, that send an email."
  type        = list(number)
  default     = [50, 75]
}

variable "kill_switch_threshold" {
  description = "Percent of the limit at which the kill switch runs (and an email is sent)."
  type        = number
  default     = 95
}

variable "domain_name" {
  description = "Subdomain the demo is served at, delegated to Route 53 from the parent domain's DNS."
  type        = string
  default     = "sqlgen.abhishek.gen.in"
}

variable "github_repository" {
  description = "owner/repo whose main branch may deploy through GitHub Actions."
  type        = string
  default     = "abhishekar10/store-procedure-generator-service"
}

variable "github_owner_id" {
  description = "Numeric ID of the repository owner; GitHub puts it in the OIDC subject."
  type        = string
  default     = "66424913"
}

variable "github_repository_id" {
  description = "Numeric ID of the repository; GitHub puts it in the OIDC subject."
  type        = string
  default     = "1456351902"
}

variable "demo_name" {
  description = "The demo stack's `name`; the kill switch targets its cluster, service and ALB by it."
  type        = string
  default     = "sp-generator"
}
