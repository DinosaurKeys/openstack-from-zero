# Data sources read existing objects. They do not adopt them into Terraform ownership.
data "openstack_identity_auth_scope_v3" "current" {
  name         = "phase6"
  set_token_id = false

  lifecycle {
    postcondition {
      condition     = self.project_id == var.existing.project_id && self.user_id == var.existing.user_id
      error_message = "The OpenStack login differs from the reviewed project/user."
    }
  }
}

data "openstack_images_image_v2" "ubuntu" {
  name        = var.existing.image_name
  owner       = var.existing.project_id
  visibility  = "private"
  disk_format = "qcow2"

  lifecycle {
    postcondition {
      condition = (self.id == var.existing.image_id && self.disk_format == "qcow2" &&
      self.min_ram_mb <= 2048 && self.min_disk_gb <= 10)
      error_message = "The image UUID, format or boot minimums differ from the approved 2-GiB/10-GiB plan."
    }
  }
}

data "openstack_compute_flavor_v2" "small" {
  flavor_id = var.existing.flavor_id
  vcpus     = 1
  ram       = 2048
  disk      = 10

  lifecycle {
    postcondition {
      condition     = self.id == var.existing.flavor_id && self.vcpus == 1 && self.ram == 2048 && self.disk == 10
      error_message = "Both VMs require the reviewed 1-vCPU, 2048-MiB, 10-GiB flavor."
    }
  }
}

data "openstack_compute_keypair_v2" "guest" {
  name    = var.existing.keypair_name
  user_id = var.existing.user_id
}

data "openstack_networking_network_v2" "private" {
  network_id = var.existing.private_network_id
  name       = "lab-private"
  tenant_id  = var.existing.project_id
  external   = false

  lifecycle {
    postcondition {
      condition = (self.id == var.existing.private_network_id && self.name == "lab-private" && self.tenant_id == var.existing.project_id &&
      !self.external && contains(self.subnets, var.existing.private_subnet_id))
      error_message = "The private network identity or subnet differs from Chapter A."
    }
  }
}

data "openstack_networking_subnet_v2" "private" {
  subnet_id    = var.existing.private_subnet_id
  network_id   = data.openstack_networking_network_v2.private.id
  cidr         = "10.10.10.0/24"
  gateway_ip   = "10.10.10.1"
  dhcp_enabled = true

  lifecycle {
    postcondition {
      condition     = self.cidr == "10.10.10.0/24" && self.gateway_ip == "10.10.10.1" && self.enable_dhcp
      error_message = "The private subnet, gateway or DHCP setting differs from the course network."
    }
  }
}

data "openstack_networking_network_v2" "external" {
  network_id = var.existing.external_network_id
  name       = "lab-external"
  external   = true

  lifecycle {
    postcondition {
      condition     = self.id == var.existing.external_network_id && self.name == "lab-external" && self.external && contains(self.subnets, var.existing.external_subnet_id)
      error_message = "The floating-IP network or subnet differs from Chapter A."
    }
  }
}
