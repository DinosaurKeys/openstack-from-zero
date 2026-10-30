variable "existing" {
  description = "Existing resources verified in Chapter A; these are references, not new resources."
  type = object({
    project_id          = string
    user_id             = string
    image_id            = string
    image_name          = string
    flavor_id           = string
    keypair_name        = string
    private_network_id  = string
    private_subnet_id   = string
    external_network_id = string
    external_subnet_id  = string
    volume_type_name    = string
  })

  validation {
    condition = alltrue([
      for id in [var.existing.project_id, var.existing.user_id] :
      can(regex("^([0-9a-fA-F]{32}|[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})$", id))
    ])
    error_message = "Project and user IDs must be verified Keystone IDs."
  }

  validation {
    condition = alltrue([
      for id in [var.existing.image_id, var.existing.private_network_id,
        var.existing.private_subnet_id, var.existing.external_network_id,
      var.existing.external_subnet_id] :
      can(regex("^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$", id))
    ])
    error_message = "Image, network and subnet references must contain verified UUIDs."
  }
}

variable "home_lan_cidr" {
  description = "Home LAN permitted to reach lab1 over SSH/HTTP."
  type        = string
  default     = "192.168.0.0/24"

  validation {
    condition     = can(cidrnetmask(var.home_lan_cidr)) && var.home_lan_cidr != "0.0.0.0/0"
    error_message = "Use the reviewed IPv4 home LAN, not unrestricted internet access."
  }
}
