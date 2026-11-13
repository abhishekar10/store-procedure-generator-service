variable "name" {
  description = "Name prefix: cluster, ALB, task family and log group use it as is; the service adds -api."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "At least two subnets in different AZs (an ALB requirement)."
  type        = list(string)
}

variable "api_image" {
  description = "API image reference, repository URL plus tag."
  type        = string
}

variable "ui_image" {
  description = "UI (nginx) image reference, repository URL plus tag."
  type        = string
}

variable "repository_arns" {
  description = "ECR repositories the execution role may pull from."
  type        = list(string)
}

variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener (issued, in this region)."
  type        = string
}

variable "api_key_secret_arn" {
  description = "Secrets Manager secret holding GEMINI_API_KEY."
  type        = string
}

variable "environment" {
  description = "Plain (non-secret) environment variables for the container."
  type        = map(string)
  default     = {}
}

variable "task_cpu" {
  description = "Fargate CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory in MiB."
  type        = number
  default     = 512
}

variable "desired_count" {
  type    = number
  default = 1
}

variable "log_retention_days" {
  type    = number
  default = 7
}
