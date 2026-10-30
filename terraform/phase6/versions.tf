terraform {
  required_version = "= 1.17.1"

  required_providers {
    openstack = {
      source  = "terraform-provider-openstack/openstack"
      version = "= 3.4.0"
    }
  }

  # Chapter D supplies private/phase6/backend.tfbackend during init.
  backend "local" {}
}
