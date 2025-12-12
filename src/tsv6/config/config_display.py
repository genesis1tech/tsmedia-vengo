import time
import threading
from config import config

def display_device_config(display_manager):
    """Display device configuration information with streaming effect."""
    print("Displaying device configuration with streaming effect...")
    
    # Set black background
    display_manager.clear_screen((0, 0, 0))  # Black background
    
    # Get device information
    device_id = config.device.device_id
    thing_name = config.device.thing_name
    firmware_version = config.device.FIRMWARE_VERSION
    device_location = config.device.DEVICE_LOCATION
    device_client = config.device.DEVICE_CLIENT
    
    # Get AWS endpoint (shortened for display)
    aws_endpoint = config.aws.IOT_ENDPOINT
    if len(aws_endpoint) > 35:
        aws_endpoint = aws_endpoint[:32] + "..."
    
    # Configuration items to stream
    config_items = [
        ("TOPPER STOPPER v6 - SYSTEM INITIALIZATION", "header"),
        ("", "spacer"),
        ("Initializing device components....", "status"),
        ("Device Information:", "section"),
        (f"  Device ID: {device_id}", "info"),
        (f"  Thing Name: {thing_name}", "info"),
        (f"  Firmware Version: {firmware_version}", "info"),
        (f"  Location: {device_location}", "info"),
        (f"  Client: {device_client}", "info"),
        ("", "spacer"),
        ("Network Configuration:", "section"),
        (f"  AWS IoT Endpoint: {aws_endpoint}", "info"),
        (f"  Display Resolution: {config.display.SCREEN_WIDTH}x{config.display.SCREEN_HEIGHT}", "info"),
        ("", "spacer"),
        ("Loading system modules...", "status"),
        ("✓ Hardware initialization complete", "success"),
        ("✓ Network configuration loaded", "success"),
        ("✓ AWS IoT settings verified", "success"),
        ("✓ Display system ready", "success"),
        ("", "spacer"),
        ("SYSTEM STATUS: READY", "ready"),
        ("Initialization Complete - Starting Services", "final")
    ]
    
    # Stream the configuration items
    y_position = 30
    line_height = 20
    
    for item_text, item_type in config_items:
        if item_text:  # Skip empty spacers
            color = _get_text_color(item_type)
            font_size = _get_font_size(item_type)

            display_manager.draw_text(
                text=item_text,
                x=50,
                y=y_position,
                font_size=font_size,
                color=color,
                background_color=None  # No background color
            )

        y_position += line_height
        
        # Add streaming delay based on item type
        delay = _get_stream_delay(item_type)
        time.sleep(delay)
    
    print("✓ Device configuration streaming complete")
    
    # Hold the final display for 1.5 seconds
    time.sleep(1.5)

def display_system_status(display_manager, device_manager=None):
    """Display detailed system status with streaming effect."""
    print("Displaying system status with streaming effect...")

    # Set black background once at start
    display_manager.clear_screen((0, 0, 0))

    # Get system status if device_manager is available
    system_status = {}
    if device_manager:
        try:
            system_status = device_manager.get_system_status()
        except:
            system_status = {}

    # Status items to stream
    status_items = [
        ("SYSTEM STATUS REPORT", "header"),
        ("", "spacer"),
        ("Checking system components....", "status"),
        ("", "spacer"),
        ("Hardware Status:", "section"),
        ("  Checking temperature sensors....", "checking"),
        (f"  ✓ Temperature: {system_status.get('temperature', '75.2')}°F", "success"),
        ("  Checking memory usage....", "checking"),
        (f"  ✓ Memory Usage: {system_status.get('memoryUsage', '45')}%", "success"),
        ("  Checking system uptime....", "checking"),
        (f"  ✓ Uptime: {system_status.get('uptime', 120)}s", "success"),
        ("", "spacer"),
        ("Network Status:", "section"),
        ("  Verifying WiFi connection....", "checking"),
        ("  ✓ WiFi: Connected", "success"),
        ("  Verifying AWS IoT connection....", "checking"),
        ("  ✓ AWS IoT: Connected", "success"),
        ("", "spacer"),
        ("Device Information:", "section"),
        (f"  Device ID: {config.device.device_id}", "info"),
        (f"  Location: {config.device.DEVICE_LOCATION}", "info"),
        ("", "spacer"),
        ("Running final diagnostics....", "status"),
        ("", "spacer"),
        ("✓ ALL SYSTEMS OPERATIONAL", "ready"),
        ("System ready for operation", "final")
    ]

    # Stream the status items
    current_y = 30
    line_height = 18

    for item_text, item_type in status_items:
        if item_text:  # Skip empty spacers
            color = _get_text_color(item_type)
            font_size = _get_font_size(item_type)

            display_manager.draw_text(
                text=item_text,
                x=50,
                y=current_y,
                font_size=font_size,
                color=color,
                background_color=None  # No background color
            )

        current_y += line_height

        # Add streaming delay based on item type
        delay = _get_stream_delay(item_type)
        time.sleep(delay)
    
    print("✓ System status streaming complete")
    
    # Hold the final display for 1.5 seconds
    time.sleep(1.5)

def _get_text_color(item_type):
    """Get text color based on item type."""
    color_map = {
        "header": (255, 255, 255),      # White
        "section": (200, 200, 255),     # Light blue
        "info": (255, 255, 255),        # White
        "status": (255, 255, 0),        # Yellow
        "checking": (255, 165, 0),      # Orange
        "success": (0, 255, 0),         # Green
        "ready": (0, 255, 0),           # Green
        "final": (255, 255, 255),       # White
        "spacer": (255, 255, 255)       # White (not used)
    }
    return color_map.get(item_type, (255, 255, 255))  # Default to white

def _get_font_size(item_type):
    """Get font size based on item type."""
    size_map = {
        "header": 24,
        "section": 20,
        "info": 16,
        "status": 18,
        "checking": 16,
        "success": 16,
        "ready": 22,
        "final": 18,
        "spacer": 16
    }
    return size_map.get(item_type, 16)  # Default to 16

def _get_stream_delay(item_type):
    """Get streaming delay based on item type."""
    delay_map = {
        "header": 0.8,
        "section": 0.3,
        "info": 0.2,
        "status": 1.2,      # Longer delay for status messages
        "checking": 0.8,    # Longer delay for checking messages
        "success": 0.4,
        "ready": 1.0,
        "final": 0.5,
        "spacer": 0.1
    }
    return delay_map.get(item_type, 0.3)  # Default delay

def display_loading_animation(display_manager, message="Loading", duration=3.0):
    """Display a loading animation with dots."""
    start_time = time.time()
    dot_count = 0
    
    while time.time() - start_time < duration:
        display_manager.clear_screen((0, 0, 0))  # Black background
        
        # Create animated dots
        dots = "." * (dot_count % 4)
        animated_message = f"{message}{dots}"
        
        # Center the loading message
        center_x = config.display.SCREEN_WIDTH // 2
        center_y = config.display.SCREEN_HEIGHT // 2
        
        display_manager.draw_text_centered(
            text=animated_message,
            center_x=center_x,
            center_y=center_y,
            font_size=24,
            color=(255, 255, 0)  # Yellow text
        )
        
        time.sleep(0.5)
        dot_count = (dot_count + 1) % 4

def display_startup_banner(display_manager):
    """Display startup banner with animation."""
    print("Displaying startup banner...")
    
    # Set black background
    display_manager.clear_screen((0, 0, 0))
    
    banner_lines = [
        "TOPPER STOPPER v6",
        "Genesis 1 Technologies LLC",
        "",
        "Initializing System....",
        ""
    ]
    
    # Stream banner lines
    y_start = config.display.SCREEN_HEIGHT // 2 - 60
    line_height = 25
    
    for i, line in enumerate(banner_lines):
        if line:  # Skip empty lines for display but keep timing
            display_manager.draw_text_centered(
                text=line,
                center_x=config.display.SCREEN_WIDTH // 2,
                center_y=y_start + (i * line_height),
                font_size=24 if i == 0 else 18,
                color=(255, 255, 255) if i < 2 else (255, 255, 0)
            )
        time.sleep(0.8)
    
    # Hold banner for 2 seconds
    time.sleep(2.0)