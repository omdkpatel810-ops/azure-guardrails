terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
    azuread = {
      source  = "hashicorp/azuread"
      version = "~> 3.4"
    }
  }

  # Separate state from platform/, so a bad environment request can never touch the policies.
  backend "azurerm" {
    key              = "vending.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {}
  storage_use_azuread = true
}

provider "azuread" {}
