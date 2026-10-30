# Terraform's mock provider makes these offline plans: no cloud login or changes.
mock_provider "openstack" {
  mock_data "openstack_identity_auth_scope_v3" {
    defaults = {
      project_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      user_id    = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
  }
  mock_data "openstack_images_image_v2" {
    defaults = {
      id          = "11111111-1111-4111-8111-111111111111"
      disk_format = "qcow2"
      min_ram_mb  = 2048
      min_disk_gb = 10
    }
  }
  mock_data "openstack_compute_flavor_v2" {
    defaults = {
      id    = "22222222-1111-4111-8111-222222222222"
      vcpus = 1
      ram   = 2048
      disk  = 10
    }
  }
  mock_data "openstack_networking_subnet_v2" {
    defaults = {
      id          = "44444444-1111-4111-8111-444444444444"
      cidr        = "10.10.10.0/24"
      gateway_ip  = "10.10.10.1"
      enable_dhcp = true
    }
  }
}

override_data {
  target = data.openstack_networking_network_v2.private
  values = {
    id        = "33333333-1111-4111-8111-333333333333"
    name      = "lab-private"
    tenant_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    external  = false
    subnets   = ["44444444-1111-4111-8111-444444444444"]
  }
}

override_data {
  target = data.openstack_networking_network_v2.external
  values = {
    id       = "55555555-1111-4111-8111-555555555555"
    name     = "lab-external"
    external = true
    subnets  = ["66666666-1111-4111-8111-666666666666"]
  }
}

variables {
  existing = {
    project_id          = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    user_id             = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    image_id            = "11111111-1111-4111-8111-111111111111"
    image_name          = "lab-ubuntu-24.04-20261017"
    flavor_id           = "22222222-1111-4111-8111-222222222222"
    keypair_name        = "lab-vm-key"
    private_network_id  = "33333333-1111-4111-8111-333333333333"
    private_subnet_id   = "44444444-1111-4111-8111-444444444444"
    external_network_id = "55555555-1111-4111-8111-555555555555"
    external_subnet_id  = "66666666-1111-4111-8111-666666666666"
    volume_type_name    = "__DEFAULT__"
  }
}

run "approved_two_vm_plan" {
  command = plan

  assert {
    condition     = toset(keys(openstack_compute_instance_v2.vm)) == toset(["lab1", "lab2"])
    error_message = "Create only the two approved VMs."
  }
  assert {
    condition     = alltrue([for vm in openstack_compute_instance_v2.vm : vm.flavor_id == var.existing.flavor_id && !vm.config_drive])
    error_message = "Reuse the checked flavor and HTTP metadata."
  }
  assert {
    condition     = length(openstack_networking_secgroup_rule_v2.app_ingress) + length(openstack_networking_secgroup_rule_v2.db_ingress) + length(openstack_networking_secgroup_rule_v2.egress) == 6
    error_message = "The reviewed quota budget is six IPv4 rules."
  }
  assert {
    condition     = openstack_networking_secgroup_rule_v2.app_ingress["ssh"].remote_ip_prefix == "192.168.0.0/24" && openstack_networking_secgroup_rule_v2.db_ingress["postgresql"].port_range_min == 5432
    error_message = "Keep app access on the home LAN and database access on PostgreSQL's port."
  }
  assert {
    condition     = alltrue([for group in openstack_networking_secgroup_v2.vm : group.delete_default_rules])
    error_message = "Manage the new groups' egress rules explicitly."
  }
  assert {
    condition     = openstack_blockstorage_volume_v3.db_data.size == 5 && openstack_blockstorage_volume_v3.db_data.name == "lab2-data"
    error_message = "Create a separate five-GiB database volume."
  }
  assert {
    condition     = openstack_networking_floatingip_v2.app.pool == "lab-external" && openstack_networking_floatingip_v2.app.subnet_id == var.existing.external_subnet_id
    error_message = "Allocate the app's new address from the checked external subnet."
  }
}

run "reject_wrong_image" {
  command = plan
  override_data {
    target = data.openstack_images_image_v2.ubuntu
    values = {
      id          = "99999999-1111-4111-8111-999999999999"
      disk_format = "qcow2"
      min_ram_mb  = 2048
      min_disk_gb = 10
    }
  }
  expect_failures = [data.openstack_images_image_v2.ubuntu]
}

run "reject_insufficient_ram" {
  command = plan
  override_data {
    target = data.openstack_compute_flavor_v2.small
    values = { vcpus = 1, ram = 1024, disk = 10 }
  }
  expect_failures = [data.openstack_compute_flavor_v2.small]
}

run "reject_higher_image_minimum" {
  command = plan
  override_data {
    target = data.openstack_images_image_v2.ubuntu
    values = {
      id          = "11111111-1111-4111-8111-111111111111"
      disk_format = "qcow2"
      min_ram_mb  = 4096
      min_disk_gb = 10
    }
  }
  expect_failures = [data.openstack_images_image_v2.ubuntu]
}

run "reject_wrong_login" {
  command = plan
  override_data {
    target = data.openstack_identity_auth_scope_v3.current
    values = { project_id = "cccccccccccccccccccccccccccccccc", user_id = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb" }
  }
  expect_failures = [data.openstack_identity_auth_scope_v3.current]
}

run "reject_wrong_private_network" {
  command = plan
  override_data {
    target = data.openstack_networking_network_v2.private
    values = {
      id        = "33333333-1111-4111-8111-333333333333"
      name      = "unrelated-network"
      tenant_id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      external  = false
      subnets   = ["44444444-1111-4111-8111-444444444444"]
    }
  }
  expect_failures = [data.openstack_networking_network_v2.private]
}
