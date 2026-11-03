terraform {
  required_version = ">= 1.9"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.40"
    }
  }

  # State lives in the storage account bootstrap/bootstrap.sh creates. The pipeline and
  # bootstrap pass the account's details with -backend-config, so none of them are in git.
  backend "azurerm" {
    key              = "platform.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  features {}
  # Management groups and policy live above any one subscription, so don't wait for
  # resource providers to register in one.
  resource_provider_registrations = "none"
  storage_use_azuread             = true
}
