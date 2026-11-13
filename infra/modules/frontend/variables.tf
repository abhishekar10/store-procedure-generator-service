variable "name" {
  type = string
}

variable "api_origin_domain" {
  description = "DNS name of the ALB that serves /v1/* and /api/*."
  type        = string
}

variable "origin_verify_header_name" {
  type    = string
  default = "X-Origin-Verify"
}

variable "origin_verify_header_value" {
  type      = string
  sensitive = true
}

variable "price_class" {
  description = "PriceClass_200 includes edge locations in India; PriceClass_100 is North America and Europe only."
  type        = string
  default     = "PriceClass_200"
}

variable "force_destroy" {
  description = "Delete the bucket even if it still holds the UI files."
  type        = bool
  default     = false
}
