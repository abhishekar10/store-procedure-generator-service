terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.6"
    }
  }

  # Local state, gitignored, like the demo stack. This stack is long-lived:
  # `make down` does not touch it.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = "sp-generator"
      Stack     = "account"
      ManagedBy = "terraform"
    }
  }
}
