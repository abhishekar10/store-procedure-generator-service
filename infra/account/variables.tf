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

variable "demo_name" {
  description = "The demo stack's `name`; the kill switch targets its cluster, service and ALB by it."
  type        = string
  default     = "sp-generator"
}
