terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # State is local (terraform.tfstate here, gitignored): one owner, brought
  # up for demos and destroyed afterwards. A shared S3 backend would be the
  # next step for more than one operator.
}

provider "aws" {
  region = var.region
  default_tags {
    tags = {
      Project   = var.name
      Stack     = "demo"
      ManagedBy = "terraform"
    }
  }
}
