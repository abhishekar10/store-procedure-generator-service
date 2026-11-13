variable "name" {
  description = "Name for the function, its role and topics."
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster of the demo stack."
  type        = string
}

variable "service_name" {
  description = "ECS service to scale to zero."
  type        = string
}

variable "alb_name" {
  description = "ALB to delete."
  type        = string
}

variable "alert_email" {
  description = "Receives the kill switch's report."
  type        = string
}
