variable "name" {
  description = "Name of the deploy role."
  type        = string
}

variable "github_repository" {
  description = "owner/repo allowed to assume the role."
  type        = string
}

variable "github_owner_id" {
  description = "Numeric ID of the repository owner (GET /repos/<owner>/<repo> -> owner.id)."
  type        = string
}

variable "github_repository_id" {
  description = "Numeric ID of the repository (GET /repos/<owner>/<repo> -> id)."
  type        = string
}

variable "branch" {
  description = "Only workflow runs on this branch may assume the role."
  type        = string
  default     = "main"
}

variable "repository_arns" {
  description = "ECR repositories the role may push to."
  type        = list(string)
}

variable "cluster_name" {
  type = string
}

variable "service_name" {
  type = string
}

variable "execution_role_name" {
  description = "The demo stack's task execution role, which a new task definition must reference."
  type        = string
}
