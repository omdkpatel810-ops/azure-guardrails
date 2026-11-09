variable "team" {
  description = "Team name, such as payments."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.team))
    error_message = "Use 2 to 21 lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "dev, test or prod."
  type        = string

  validation {
    condition     = contains(["dev", "test", "prod"], var.environment)
    error_message = "Use dev, test or prod."
  }
}

variable "owner" {
  description = "Email address of the person accountable for this environment. Budget alerts go here."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.owner))
    error_message = "The owner must be an email address."
  }
}

variable "cost_center" {
  description = "Cost centre the spend is charged to, such as cc-2040."
  type        = string

  validation {
    condition     = can(regex("^cc-[0-9]{4}$", var.cost_center))
    error_message = "Use a cost centre like cc-2040."
  }
}

variable "location" {
  description = "Region for the resource group and network."
  type        = string
  default     = "canadacentral"
}

variable "monthly_budget" {
  description = "Monthly budget in the billing currency. Alerts at 50%, 80% and 100% of actual spend, and 100% forecast."
  type        = number

  validation {
    condition     = var.monthly_budget > 0
    error_message = "The budget must be more than zero."
  }
}

variable "contributor_groups" {
  description = "Entra ID groups given Contributor, by display name => object ID. Not allowed in prod."
  type        = map(string)
  default     = {}

  validation {
    condition     = var.environment != "prod" || length(var.contributor_groups) == 0
    error_message = "Prod doesn't give people Contributor; changes go through pipelines. Give the group Reader instead."
  }
}

variable "reader_groups" {
  description = "Entra ID groups given Reader, by display name => object ID."
  type        = map(string)
  default     = {}
}

variable "address_space" {
  description = "Address space for the environment's virtual network, such as 10.100.1.0/24. Null means no network."
  type        = string
  default     = null

  validation {
    condition     = var.address_space == null || can(cidrhost(var.address_space, 0))
    error_message = "Use a network such as 10.100.1.0/24."
  }
}

variable "request_file" {
  description = "The request this environment came from, recorded in its tags."
  type        = string

  validation {
    condition     = var.request_file == "${var.team}-${var.environment}.json"
    error_message = "Name the request file <team>-<environment>.json."
  }
}
