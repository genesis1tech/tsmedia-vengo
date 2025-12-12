#!/bin/bash
# Script to configure pigpiod service with reduced timeout for faster shutdowns

echo "Configuring pigpiod service timeout..."

# Create systemd override directory
sudo mkdir -p /etc/systemd/system/pigpiod.service.d

# Create timeout override file
sudo tee /etc/systemd/system/pigpiod.service.d/timeout.conf << 'EOC'
[Service]
TimeoutStopSec=10s
EOC

# Reload systemd configuration
sudo systemctl daemon-reload

# Verify the change
echo "Timeout configuration applied:"
systemctl show pigpiod.service | grep TimeoutStopUSec

echo "pigpiod service will now stop within 10 seconds instead of 90 seconds during shutdown/restart."
