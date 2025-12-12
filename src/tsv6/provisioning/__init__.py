"""
WiFi Provisioning Module for TSV6
=================================

Provides WiFi provisioning functionality for first-boot setup.
Creates a hotspot with captive portal for end-users to enter WiFi credentials.
"""

from .wifi_provisioner import WiFiProvisioner, ProvisioningResult

__all__ = ["WiFiProvisioner", "ProvisioningResult"]
