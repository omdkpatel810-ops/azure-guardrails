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
  # bootstrap.sh starts this with local state, then moves it into the storage account it creates.
}

provider "azurerm" {
  features {}
  storage_use_azuread = true
}

provider "azuread" {}
