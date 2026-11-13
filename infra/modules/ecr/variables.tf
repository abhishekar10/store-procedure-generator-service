variable "name" {
  description = "Repository name."
  type        = string
}

variable "keep_images" {
  description = "How many images to keep; older ones expire."
  type        = number
  default     = 5
}

variable "force_delete" {
  description = "Delete the repository even if it still holds images (for a stack that is destroyed after demos)."
  type        = bool
  default     = false
}
