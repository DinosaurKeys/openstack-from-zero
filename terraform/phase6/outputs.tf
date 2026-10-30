output "vms" {
  description = "VM IDs, roles and private addresses. Read actual compute hosts from Nova after creation."
  value = {
    for name, vm in openstack_compute_instance_v2.vm : name => {
      id         = vm.id
      role       = local.vms[name]
      private_ip = one(openstack_networking_port_v2.vm[name].all_fixed_ips)
    }
  }
}

output "app_floating_ip" {
  description = "New lab1 SSH/browser address; HTTP becomes available after Phase 7 installs NGINX."
  value       = openstack_networking_floatingip_v2.app.address
}

output "database_volume" {
  description = "New blank lab2 data volume. Read its actual storage backend from Cinder after creation."
  value = {
    id             = openstack_blockstorage_volume_v3.db_data.id
    name           = openstack_blockstorage_volume_v3.db_data.name
    size_gib       = openstack_blockstorage_volume_v3.db_data.size
    attached_vm_id = openstack_compute_volume_attach_v2.db_data.instance_id
    attachment_id  = openstack_compute_volume_attach_v2.db_data.id
  }
}
