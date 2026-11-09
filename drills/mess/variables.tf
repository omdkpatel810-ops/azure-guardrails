variable "baseline_assignment_id" {
  description = "Resource ID of the landing-zones/gr-baseline policy assignment, which the seeding exemption suspends."
  type        = string
}

variable "owner" {
  description = "Who runs the drill: owner of the resource group and of the seeding exemption."
  type        = string

  validation {
    condition     = can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.owner))
    error_message = "Use an email address."
  }
}

variable "ssh_public_key" {
  description = "Public key for the drill VMs. drills/drill.py makes one; nobody signs in anyway."
  type        = string
}

variable "location" {
  description = "Region for the mess. Must be one Guardrails allows."
  type        = string
  default     = "canadacentral"
}

variable "exempt" {
  description = "True while seeding: the exemption lets the mess exist, as if it predated the guardrails."
  type        = bool
  default     = true
}

variable "fixed" {
  description = "True once the drill fixes what policy can't: closes the storage, deletes idle resources, shrinks the database."
  type        = bool
  default     = false
}
