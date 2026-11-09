terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
  }
  # Local state on purpose: the drill lives for an hour or two in Cloud Shell, then is destroyed.
}

provider "azurerm" {
  features {
    resource_group {
      prevent_deletion_if_contains_resources = false # remediation may have added resources' tags only
    }
  }
}
