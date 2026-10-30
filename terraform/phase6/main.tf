locals {
  vms = {
    lab1 = "application"
    lab2 = "database"
  }
  metadata = {
    project    = "openstack-from-zero"
    phase      = "6"
    managed_by = "terraform"
  }
}

resource "openstack_networking_secgroup_v2" "vm" {
  for_each             = local.vms
  name                 = "${each.key}-access"
  description          = "Phase 6 ${each.value} access"
  tenant_id            = var.existing.project_id
  delete_default_rules = true

  # Check all reused identities before creating the first new cloud objects.
  depends_on = [
    data.openstack_identity_auth_scope_v3.current,
    data.openstack_images_image_v2.ubuntu,
    data.openstack_compute_flavor_v2.small,
    data.openstack_compute_keypair_v2.guest,
    data.openstack_networking_subnet_v2.private,
    data.openstack_networking_network_v2.external,
  ]
}

resource "openstack_networking_secgroup_rule_v2" "app_ingress" {
  for_each          = { ssh = 22, http = 80 }
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = each.value
  port_range_max    = each.value
  remote_ip_prefix  = var.home_lan_cidr
  security_group_id = openstack_networking_secgroup_v2.vm["lab1"].id
}

resource "openstack_networking_secgroup_rule_v2" "db_ingress" {
  for_each          = { ssh = 22, postgresql = 5432 }
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = each.value
  port_range_max    = each.value
  remote_group_id   = openstack_networking_secgroup_v2.vm["lab1"].id
  security_group_id = openstack_networking_secgroup_v2.vm["lab2"].id
}

resource "openstack_networking_secgroup_rule_v2" "egress" {
  for_each          = local.vms
  direction         = "egress"
  ethertype         = "IPv4"
  security_group_id = openstack_networking_secgroup_v2.vm[each.key].id
}

resource "openstack_networking_port_v2" "vm" {
  for_each              = local.vms
  name                  = "${each.key}-port"
  network_id            = data.openstack_networking_network_v2.private.id
  tenant_id             = var.existing.project_id
  admin_state_up        = true
  port_security_enabled = true
  security_group_ids    = [openstack_networking_secgroup_v2.vm[each.key].id]

  fixed_ip {
    subnet_id = data.openstack_networking_subnet_v2.private.id
  }
}

resource "openstack_compute_instance_v2" "vm" {
  for_each     = local.vms
  name         = each.key
  image_id     = data.openstack_images_image_v2.ubuntu.id
  flavor_id    = data.openstack_compute_flavor_v2.small.id
  key_pair     = data.openstack_compute_keypair_v2.guest.name
  config_drive = false
  metadata     = merge(local.metadata, { role = each.value })

  # Security groups belong to the Neutron port when using an existing port.
  network {
    port = openstack_networking_port_v2.vm[each.key].id
  }

  depends_on = [
    openstack_networking_secgroup_rule_v2.app_ingress,
    openstack_networking_secgroup_rule_v2.db_ingress,
    openstack_networking_secgroup_rule_v2.egress,
  ]

  lifecycle {
    prevent_destroy = true
  }
}

resource "openstack_networking_floatingip_v2" "app" {
  pool        = data.openstack_networking_network_v2.external.name
  subnet_id   = var.existing.external_subnet_id
  tenant_id   = var.existing.project_id
  description = "openstack-from-zero lab1"
  depends_on  = [openstack_compute_instance_v2.vm["lab1"]]
}

resource "openstack_networking_floatingip_associate_v2" "app" {
  floating_ip = openstack_networking_floatingip_v2.app.address
  port_id     = openstack_networking_port_v2.vm["lab1"].id
}

resource "openstack_blockstorage_volume_v3" "db_data" {
  name        = "lab2-data"
  description = "Phase 7 PostgreSQL data; blank until Ansible initializes it"
  size        = 5
  volume_type = var.existing.volume_type_name
  metadata    = local.metadata

  depends_on = [openstack_networking_secgroup_v2.vm]

  lifecycle {
    prevent_destroy = true
  }
}

resource "openstack_compute_volume_attach_v2" "db_data" {
  instance_id = openstack_compute_instance_v2.vm["lab2"].id
  volume_id   = openstack_blockstorage_volume_v3.db_data.id
  # Let Nova choose the device; Phase 7 identifies it by volume UUID.
}
