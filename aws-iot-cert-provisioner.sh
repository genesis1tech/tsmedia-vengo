#!/bin/bash

################################################################################
# AWS IoT Certificate Provisioner for TSV6 Raspberry Pi Devices
# Automatically provisions IoT certificates using device serial number
################################################################################

set -e

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Logging functions
log() {
    echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"
}

warning() {
    echo -e "${YELLOW}[WARNING] $1${NC}"
}

error() {
    echo -e "${RED}[ERROR] $1${NC}"
}

info() {
    echo -e "${BLUE}[INFO] $1${NC}"
}


# Check required dependencies
check_dependencies() {
    local missing_deps=()
    
    # Check for jq
    if ! command -v jq &> /dev/null; then
        missing_deps+=("jq")
    fi
    
    # Check for aws cli
    if ! command -v aws &> /dev/null; then
        missing_deps+=("awscli")
    fi
    
    # Check for curl
    if ! command -v curl &> /dev/null; then
        missing_deps+=("curl")
    fi
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        error "Missing required dependencies: ${missing_deps[*]}"
        echo "Install with: sudo apt-get install -y ${missing_deps[*]}"
        exit 1
    fi
}

# Configuration
POLICY_NAME="TSV6DevicePolicy"
CERTS_DIR="assets/certs"

# Get device serial number automatically
get_device_serial() {
    local serial=""
    
    # Try multiple methods to get serial number
    if [[ -f /proc/cpuinfo ]]; then
        serial=$(grep -i "serial" /proc/cpuinfo | cut -d: -f2 | tr -d ' \t')
    fi
    
    # Fallback to device tree if available
    if [[ -z "$serial" && -f /sys/firmware/devicetree/base/serial-number ]]; then
        serial=$(cat /sys/firmware/devicetree/base/serial-number | tr -d '\0')
    fi
    
    # Final fallback - use MAC address
    if [[ -z "$serial" ]]; then
        local mac=$(cat /sys/class/net/eth0/address 2>/dev/null || cat /sys/class/net/wlan0/address 2>/dev/null || echo "000000000000")
        serial=$(echo "$mac" | tr -d ':' | tr '[:lower:]' '[:upper:]')
    fi
    
    # Get last 8 characters
    echo "${serial: -8}"
}

# Check AWS CLI configuration
check_aws_config() {
    info "Checking AWS CLI configuration..."
    
    if ! command -v aws >/dev/null 2>&1; then
        error "AWS CLI is not installed"
        exit 1
    fi
    
    if ! aws sts get-caller-identity >/dev/null 2>&1; then
        error "AWS credentials not configured. Run 'aws configure'"
        exit 1
    fi
    
    local account=$(aws sts get-caller-identity --query Account --output text)
    local region=$(aws configure get region)
    
    if [[ -z "$region" ]]; then
        warning "AWS region not set, using us-east-1"
        aws configure set region us-east-1
        region="us-east-1"
    fi
    
    info "✓ AWS Account: $account"
    info "✓ AWS Region: $region"
}

# Create IoT policy if it doesn't exist
create_iot_policy() {
    info "Creating/verifying IoT policy: $POLICY_NAME"
    
    if aws iot get-policy --policy-name "$POLICY_NAME" >/dev/null 2>&1; then
        info "✓ Policy $POLICY_NAME already exists"
        return
    fi
    
    local policy_document='{
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "iot:Connect",
                    "iot:Publish",
                    "iot:Subscribe",
                    "iot:Receive"
                ],
                "Resource": "*"
            },
            {
                "Effect": "Allow",
                "Action": [
                    "iot:GetThingShadow",
                    "iot:UpdateThingShadow",
                    "iot:DeleteThingShadow"
                ],
                "Resource": "arn:aws:iot:*:*:thing/${iot:Connection.Thing.ThingName}"
            }
        ]
    }'
    
    aws iot create-policy \
        --policy-name "$POLICY_NAME" \
        --policy-document "$policy_document"
    
    info "✓ Created IoT policy: $POLICY_NAME"
}

# Create IoT Thing
create_iot_thing() {
    local thing_name="$1"
    local device_serial="$2"
    
    info "Creating IoT Thing: $thing_name"
    
    if aws iot describe-thing --thing-name "$thing_name" >/dev/null 2>&1; then
        info "✓ Thing $thing_name already exists"
        return
    fi
    
    # Create a temporary file for the attribute payload
    local temp_file=$(mktemp)
    cat > "$temp_file" << ATTR_EOF
{
    "attributes": {
        "deviceType": "RaspberryPi",
        "deviceSerial": "$device_serial",
        "createdBy": "aws-iot-cert-provisioner"
    }
}
ATTR_EOF
    
    aws iot create-thing \
        --thing-name "$thing_name" \
        --attribute-payload file://"$temp_file"
    
    rm "$temp_file"
    
    info "✓ Created IoT Thing: $thing_name"
}

# Generate device certificates
generate_device_certificates() {
    local thing_name="$1"
    local device_serial="$2"
    
    info "Generating certificates for $thing_name..."
    
    # Create certificates directory
    mkdir -p "$CERTS_DIR"
    
    # Create keys and certificate
    local cert_response=$(aws iot create-keys-and-certificate --set-as-active)
    
    # Check if certificate creation was successful
    if [ -z "$cert_response" ] || echo "$cert_response" | grep -q "error"; then
        echo "Error: Failed to create certificate"
        echo "$cert_response"
        return 1
    fi
    
    # Extract certificate ARN and data
    local cert_arn=$(echo "$cert_response" | jq -r '.certificateArn')
    local cert_id=$(echo "$cert_response" | jq -r '.certificateId')
    local certificate_pem=$(echo "$cert_response" | jq -r '.certificatePem')
    local private_key=$(echo "$cert_response" | jq -r '.keyPair.PrivateKey')
    local public_key=$(echo "$cert_response" | jq -r '.keyPair.PublicKey')
    
    # Validate extracted values
    if [ -z "$cert_arn" ] || [ "$cert_arn" = "null" ]; then
        echo "Error: Failed to extract certificate ARN from response"
        echo "Response: $cert_response"
        return 1
    fi
    
    # Save certificate files with the exact names expected by the code
    echo "$certificate_pem" > "$CERTS_DIR/aws_cert_crt.pem"
    echo "$private_key" > "$CERTS_DIR/aws_cert_private.pem"
    echo "$public_key" > "$CERTS_DIR/aws_cert_public.pem"
    
    # Download Amazon Root CA
    curl -s --connect-timeout 15 --max-time 30 https://www.amazontrust.com/repository/AmazonRootCA1.pem > "$CERTS_DIR/aws_cert_ca.pem"
    
    # Attach policy to certificate
    aws iot attach-policy \
        --policy-name "$POLICY_NAME" \
        --target "$cert_arn"
    
    # Attach certificate to thing
    aws iot attach-thing-principal \
        --thing-name "$thing_name" \
        --principal "$cert_arn"
    
    # Get IoT endpoint
    local iot_endpoint=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS --query endpointAddress --output text)
    
    # Set proper permissions
    chmod 600 "$CERTS_DIR"/aws_cert_*.pem
    
    info "✓ Certificates saved to $CERTS_DIR/"
    info "✓ Certificate ID: $cert_id"
    info "✓ IoT Endpoint: $iot_endpoint"
    
    # Create device configuration file
    cat > "$CERTS_DIR/device-config.json" << CONFIG_EOF
{
    "thingName": "$thing_name",
    "deviceSerial": "$device_serial",
    "certificateId": "$cert_id",
    "iotEndpoint": "$iot_endpoint",
    "region": "$(aws configure get region)",
    "certificateFiles": {
        "certificate": "aws_cert_crt.pem",
        "privateKey": "aws_cert_private.pem",
        "rootCA": "aws_cert_ca.pem"
    }
}
CONFIG_EOF
    
    info "✓ Device configuration saved to $CERTS_DIR/device-config.json"
}

# Main execution
main() {
    log "Starting AWS IoT Certificate Provisioner for TSV6..."
    
    # Check dependencies
    check_dependencies
    
    # Get device serial automatically
    local device_serial=$(get_device_serial)
    if [[ -z "$device_serial" ]]; then
        error "Could not determine device serial number"
        exit 1
    fi
    
    # Create thing name with TS_ prefix and uppercase format
    local thing_name="TS_${device_serial^^}"
    
    info "Device Serial: $device_serial"
    info "Thing Name: $thing_name"
    
    # Check AWS configuration
    check_aws_config
    
    # Create IoT policy
    create_iot_policy
    
    # Create IoT Thing
    create_iot_thing "$thing_name" "$device_serial"
    
    # Generate certificates
    generate_device_certificates "$thing_name" "$device_serial"
    
    log "✓ AWS IoT provisioning completed successfully!"
    echo
    info "Certificate files created:"
    info "  - $CERTS_DIR/aws_cert_crt.pem"
    info "  - $CERTS_DIR/aws_cert_private.pem"
    info "  - $CERTS_DIR/aws_cert_ca.pem"
    info "  - $CERTS_DIR/device-config.json"
    echo
    info "Thing Name: $thing_name"
    info "Device ready for AWS IoT communication"
}

# Run main function
main "$@"

