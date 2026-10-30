# Load the existing private public-openrc.sh in Chapter D.
# Password and CA settings come from that environment, never from this file.
provider "openstack" {
  tenant_id      = var.existing.project_id
  user_id        = var.existing.user_id
  endpoint_type  = "public"
  insecure       = false
  enable_logging = false
  max_retries    = 0
}
