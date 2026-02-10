#!/usr/bin/env python3
"""
WiFi Provisioning UI Service for TSV6

Displays a fullscreen UI when WiFi is not configured or connection is lost,
guiding users through the setup process via a captive portal.

This service is triggered:
1. On boot when no WiFi is configured
2. When network monitor exhausts recovery attempts
"""

import os
import sys
import io
import time
import subprocess
import logging
import threading
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / 'src'))

import tkinter as tk
from PIL import Image, ImageTk
import qrcode

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class WiFiProvisioningUI:
    """
    Fullscreen UI for WiFi provisioning.

    Displays instructions, QR code, and status updates while the
    WiFi provisioner handles the captive portal in the background.
    """

    # Colors - Dark blue theme
    BG_COLOR = '#1a1a2e'
    CARD_COLOR = '#16213e'
    TEXT_COLOR = '#ffffff'
    SUBTITLE_COLOR = '#a0a0a0'
    ACCENT_COLOR = '#0f3460'
    SUCCESS_COLOR = '#4CAF50'
    ERROR_COLOR = '#f44336'

    def __init__(self):
        self.root = None
        self.provisioner = None
        self.status_label = None
        self.qr_label = None
        self._setup_display()

    def _setup_display(self):
        """Setup display environment"""
        if not os.environ.get('DISPLAY'):
            if os.path.exists('/tmp/.X11-unix/X0'):
                os.environ['DISPLAY'] = ':0'
                logger.info('DISPLAY set to :0')
            else:
                logger.error('No display available')
                sys.exit(1)

    def _get_device_id(self) -> str:
        """Get unique device ID from Raspberry Pi serial number"""
        try:
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('Serial'):
                        serial = line.split(':')[1].strip()
                        return serial[-8:].upper()
        except Exception as e:
            logger.warning(f"Could not read device serial: {e}")
        return "UNKNOWN"

    def _generate_qr_code(self, url: str, size: int = 150) -> ImageTk.PhotoImage:
        """Generate QR code image for the given URL"""
        try:
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=2,
            )
            qr.add_data(url)
            qr.make(fit=True)

            # Create image with white background
            img = qr.make_image(fill_color="black", back_color="white")

            # Resize
            img = img.resize((size, size), Image.Resampling.LANCZOS)

            return ImageTk.PhotoImage(img)
        except Exception as e:
            logger.error(f"Failed to generate QR code: {e}")
            return None

    def _init_provisioner(self):
        """Initialize WiFi provisioner in background"""
        try:
            from tsv6.provisioning.wifi_provisioner import WiFiProvisioner, ProvisioningConfig

            config = ProvisioningConfig()
            self.provisioner = WiFiProvisioner(config)

            # Get connection info
            self.ap_ssid = self.provisioner.ap_ssid
            self.ap_password = self.provisioner.config.ap_password
            self.ap_ip = self.provisioner.config.ap_ip

            logger.info(f'Provisioner initialized: SSID={self.ap_ssid}')
        except Exception as e:
            logger.error(f'Failed to initialize provisioner: {e}')
            self.provisioner = None

    def _start_provisioning_background(self):
        """Start hotspot and web server in background"""
        def run_provisioning():
            if not self.provisioner:
                self._update_status("Error: Provisioner not initialized", error=True)
                return

            try:
                # Check if already connected
                if not self.provisioner.needs_provisioning():
                    logger.info("WiFi already configured - exiting")
                    self._update_status("WiFi already connected!", success=True)
                    time.sleep(2)
                    self.root.after(0, self._on_success)
                    return

                # Start access point
                self._update_status("Starting WiFi hotspot...")
                if not self.provisioner._start_access_point():
                    self._update_status("Failed to start hotspot", error=True)
                    return

                # Start web server
                self._update_status("Starting configuration portal...")
                self.provisioner._start_web_server()

                self._update_status("Waiting for connection...")

                # Wait for credentials — no timeout. The wifi-wait.sh gate
                # will stop this service once connectivity is confirmed.
                while not self.provisioner.credentials_received.is_set():
                    # Check if shutdown requested (SIGTERM from systemctl stop)
                    if self.provisioner.shutdown_flag.is_set():
                        return

                    time.sleep(1)

                # Credentials received
                if self.provisioner.wifi_credentials:
                    ssid = self.provisioner.wifi_credentials['ssid']
                    password = self.provisioner.wifi_credentials['password']

                    self._update_status(f"Connecting to {ssid}...")

                    if self.provisioner._apply_wifi_config(ssid, password):
                        self._update_status("Connected successfully!", success=True)
                        time.sleep(2)
                        self.root.after(0, self._on_success)
                    else:
                        self._update_status("Connection failed - please try again", error=True)
                        # Reset for retry
                        self.provisioner.credentials_received.clear()
                        self.provisioner.wifi_credentials = None
                        self.provisioner._start_access_point()
                        self._update_status("Waiting for connection...")

            except Exception as e:
                logger.error(f"Provisioning error: {e}")
                self._update_status(f"Error: {str(e)[:50]}", error=True)

        thread = threading.Thread(target=run_provisioning, daemon=True)
        thread.start()

    def _update_status(self, message: str, success: bool = False, error: bool = False):
        """Update status label on UI thread"""
        def update():
            if self.status_label:
                self.status_label.config(text=message)
                if success:
                    self.status_label.config(fg=self.SUCCESS_COLOR)
                elif error:
                    self.status_label.config(fg=self.ERROR_COLOR)
                else:
                    self.status_label.config(fg=self.SUBTITLE_COLOR)

        if self.root:
            self.root.after(0, update)

    def _on_success(self):
        """Handle successful WiFi configuration"""
        logger.info("WiFi configured successfully - closing UI")

        # Cleanup provisioner (tear down AP, return wlan0 to NM)
        if self.provisioner:
            self.provisioner._stop_access_point()

        # Close UI — wifi-wait.sh will detect connectivity and release tsv6.service
        self.root.quit()
        self.root.destroy()

    def run(self):
        """Run the WiFi provisioning UI"""
        logger.info('Starting WiFi Provisioning UI')

        # Initialize provisioner first to get SSID/password
        self._init_provisioner()

        if not self.provisioner:
            logger.error('Failed to initialize provisioner')
            return

        # Create the main window
        self.root = tk.Tk()
        self.root.title('WiFi Setup')
        self.root.configure(bg=self.BG_COLOR)

        # Get screen dimensions
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        logger.info(f'Screen dimensions: {screen_width}x{screen_height}')

        # Set window geometry
        self.root.geometry(f'{screen_width}x{screen_height}+0+0')

        # Make fullscreen and topmost
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        self.root.lift()
        self.root.focus_force()

        # Hide mouse cursor
        self.root.config(cursor='none')

        # Create main container
        container = tk.Frame(self.root, bg=self.BG_COLOR)
        container.place(relx=0.5, rely=0.5, anchor='center')

        # WiFi icon
        wifi_icon = tk.Label(
            container,
            text='\u2630',  # WiFi-like symbol
            font=('Helvetica', 48),
            fg=self.TEXT_COLOR,
            bg=self.BG_COLOR
        )
        wifi_icon.pack(pady=(0, 10))

        # Topper Stopper branding
        brand_label = tk.Label(
            container,
            text='Topper Stopper',
            font=('Helvetica', 24, 'bold'),
            fg=self.TEXT_COLOR,
            bg=self.BG_COLOR
        )
        brand_label.pack(pady=(0, 5))

        # Title
        title_label = tk.Label(
            container,
            text='WiFi Setup Required',
            font=('Helvetica', 20),
            fg=self.SUBTITLE_COLOR,
            bg=self.BG_COLOR
        )
        title_label.pack(pady=(0, 20))

        # Content frame (horizontal layout for QR and info)
        content_frame = tk.Frame(container, bg=self.BG_COLOR)
        content_frame.pack(pady=10)

        # QR Code section
        qr_frame = tk.Frame(content_frame, bg=self.CARD_COLOR, padx=15, pady=15)
        qr_frame.pack(side=tk.LEFT, padx=20)

        # Generate and display QR code
        qr_url = f'http://{self.ap_ip}'
        qr_image = self._generate_qr_code(qr_url, size=120)

        if qr_image:
            self.qr_label = tk.Label(qr_frame, image=qr_image, bg=self.CARD_COLOR)
            self.qr_label.image = qr_image  # Keep reference
            self.qr_label.pack()

        qr_hint = tk.Label(
            qr_frame,
            text='Scan to connect',
            font=('Helvetica', 10),
            fg=self.SUBTITLE_COLOR,
            bg=self.CARD_COLOR
        )
        qr_hint.pack(pady=(5, 0))

        # Network info section
        info_frame = tk.Frame(content_frame, bg=self.CARD_COLOR, padx=20, pady=15)
        info_frame.pack(side=tk.LEFT, padx=20)

        # Network name
        network_label = tk.Label(
            info_frame,
            text='Network:',
            font=('Helvetica', 12),
            fg=self.SUBTITLE_COLOR,
            bg=self.CARD_COLOR,
            anchor='w'
        )
        network_label.pack(fill='x')

        network_value = tk.Label(
            info_frame,
            text=self.ap_ssid,
            font=('Helvetica', 16, 'bold'),
            fg=self.TEXT_COLOR,
            bg=self.CARD_COLOR,
            anchor='w'
        )
        network_value.pack(fill='x', pady=(0, 15))

        # Password
        password_label = tk.Label(
            info_frame,
            text='Password:',
            font=('Helvetica', 12),
            fg=self.SUBTITLE_COLOR,
            bg=self.CARD_COLOR,
            anchor='w'
        )
        password_label.pack(fill='x')

        password_value = tk.Label(
            info_frame,
            text=self.ap_password,
            font=('Helvetica', 16, 'bold'),
            fg=self.TEXT_COLOR,
            bg=self.CARD_COLOR,
            anchor='w'
        )
        password_value.pack(fill='x')

        # Instructions
        instructions_frame = tk.Frame(container, bg=self.BG_COLOR)
        instructions_frame.pack(pady=20)

        steps = [
            "1. Connect to the WiFi network above",
            "2. Open any browser - you'll be redirected",
            "3. Enter your home/office WiFi credentials"
        ]

        for step in steps:
            step_label = tk.Label(
                instructions_frame,
                text=step,
                font=('Helvetica', 12),
                fg=self.SUBTITLE_COLOR,
                bg=self.BG_COLOR
            )
            step_label.pack(anchor='w', pady=2)

        # Status label
        self.status_label = tk.Label(
            container,
            text='Initializing...',
            font=('Helvetica', 14),
            fg=self.SUBTITLE_COLOR,
            bg=self.BG_COLOR
        )
        self.status_label.pack(pady=(20, 0))

        # Bind escape key for testing
        self.root.bind('<Escape>', lambda e: self._emergency_exit())

        logger.info('UI ready, starting provisioning')

        # Start provisioning in background
        self.root.after(500, self._start_provisioning_background)

        # Run the main loop
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            logger.info('Keyboard interrupt received')
        finally:
            self._cleanup()

    def _emergency_exit(self):
        """Emergency exit handler"""
        logger.warning('Emergency exit triggered')
        self._cleanup()
        self.root.quit()
        self.root.destroy()

    def _cleanup(self):
        """Cleanup resources"""
        if self.provisioner:
            try:
                self.provisioner._stop_access_point()
            except:
                pass


def _should_skip_wifi_provisioning() -> bool:
    """
    Check if WiFi provisioning should be skipped because LTE is the primary connection.

    When LTE is enabled and is the primary connection mode, WiFi provisioning
    should not run - the device will use LTE for connectivity.

    Returns:
        True if WiFi provisioning should be skipped, False otherwise
    """
    # Check connectivity mode from environment
    connectivity_mode = os.environ.get('TSV6_CONNECTIVITY_MODE', '').lower()
    lte_enabled = os.environ.get('TSV6_LTE_ENABLED', 'false').lower() == 'true'

    # Skip WiFi provisioning if LTE is the primary connection mode
    if connectivity_mode in ('lte_only', 'lte_primary_wifi_backup'):
        logger.info(
            f"LTE is primary connection mode ({connectivity_mode}) - "
            "skipping WiFi provisioning UI"
        )
        return True

    # Also skip if LTE is explicitly enabled (even without mode set)
    if lte_enabled and not connectivity_mode:
        logger.info(
            "LTE is enabled (TSV6_LTE_ENABLED=true) - "
            "skipping WiFi provisioning UI"
        )
        return True

    return False


def main():
    """Main entry point"""
    print('=' * 60)
    print('  TSV6 WiFi Provisioning UI Service')
    print('=' * 60)

    # CRITICAL: Skip WiFi provisioning if LTE is the primary connection mode
    # This ensures the WiFi setup screen doesn't show when using 4G LTE
    if _should_skip_wifi_provisioning():
        print('LTE is primary connection - WiFi provisioning not needed')
        print('Exiting WiFi provisioning UI service')
        sys.exit(0)

    try:
        ui = WiFiProvisioningUI()
        ui.run()
    except Exception as e:
        logger.error(f'WiFi provisioning UI failed: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print('WiFi provisioning UI exited')


if __name__ == '__main__':
    main()
