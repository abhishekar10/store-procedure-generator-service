variable "region" {
  type    = string
  default = "ap-south-1"
}

variable "name" {
  description = "Name prefix. The account stack's kill switch targets resources by this name."
  type        = string
  default     = "sp-generator"
}

variable "domain_name" {
  description = "Where the demo is served. Its Route 53 zone and certificate come from the account stack."
  type        = string
  default     = "sqlgen.abhishek.gen.in"
}

variable "image_tag" {
  description = "Tag of the API and UI images in ECR (the git commit; make up sets it)."
  type        = string
}

variable "desired_count" {
  description = "Number of API tasks."
  type        = number
  default     = 1
}

variable "task_cpu" {
  description = "Fargate CPU units. 256 = 0.25 vCPU: measured peak was ~27% of one core during two concurrent generations."
  type        = number
  default     = 256
}

variable "task_memory" {
  description = "Fargate memory in MiB. Measured peak was 143 MiB during two concurrent generations."
  type        = number
  default     = 512
}
