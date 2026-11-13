variable "name" {
  description = "Name prefix: cluster, ALB and log group use it as is; the service and target group add -api."
  type        = string
}

variable "vpc_id" {
  type = string
}

variable "subnet_ids" {
  description = "At least two subnets in different AZs (an ALB requirement)."
  type        = list(string)
}

variable "image" {
  description = "Full image reference, repository URL plus tag."
  type        = string
}

variable "repository_arn" {
  description = "ECR repository the execution role may pull from."
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

variable "container_port" {
  type    = number
  default = 8000
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

variable "origin_verify_header_name" {
  description = "Header CloudFront adds to every origin request; the ALB forwards only requests that carry it."
  type        = string
  default     = "X-Origin-Verify"
}

variable "origin_verify_header_value" {
  description = "Its value. Not a credential for users -- it only proves a request came through this distribution."
  type        = string
  sensitive   = true
}
