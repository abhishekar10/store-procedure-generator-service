variable "name" {
  description = "Name prefix for every resource."
  type        = string
}

variable "cidr_block" {
  description = "VPC CIDR. Two /24 public subnets are carved from it."
  type        = string
  default     = "10.40.0.0/16"
}
