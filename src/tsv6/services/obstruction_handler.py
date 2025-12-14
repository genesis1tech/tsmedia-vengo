#!/usr/bin/env python3
"""
Obstruction Handler Service for TSV6

Displays a fullscreen UI when the device is obstructed, allowing
the user to clear the obstruction and restart the main service.

This service is triggered when an obstruction is detected and the
main tsv6.service needs to be stopped for user intervention.
"""

import os
import sys
import time
import subprocess
import logging
import datetime
from pathlib import Path

# Add project paths
project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root / 'src'))

import tkinter as tk
from tkinter import font as tkfont

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class ObstructionHandlerUI:
    """
    Fullscreen UI for handling device obstruction.

    Displays message and button for user to clear obstruction
    and restart the main service.
    """

    def __init__(self):
        self.root = None
        self.servo_controller = None
        self.aws_manager = None
        self.aws_config = None
        self._setup_display()
        self._init_servo()
        self._init_aws()

    def _setup_display(self):
        """Setup display environment"""
        if not os.environ.get('DISPLAY'):
            if os.path.exists('/tmp/.X11-unix/X0'):
                os.environ['DISPLAY'] = ':0'
                logger.info('DISPLAY set to :0')
            else:
                logger.error('No display available')
                sys.exit(1)

    def _init_servo(self):
        """Initialize servo controller"""
        try:
            # Add vendor path for servo SDK
            vendor_path = project_root / 'src/tsv6/hardware/stservo/vendor'
            if str(vendor_path) not in sys.path:
                sys.path.insert(0, str(vendor_path))

            from tsv6.hardware.stservo.controller import STServoController
            self.servo_controller = STServoController()
            logger.info('Servo controller initialized')
        except Exception as e:
            logger.error(f'Failed to initialize servo: {e}')
            self.servo_controller = None

    def _init_aws(self):
        """Initialize AWS IoT connection for status publishing"""
        try:
            from tsv6.config.production_config import ProductionConfigManager
            from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig

            config_manager = ProductionConfigManager()
            self.aws_config = config_manager.get_aws_config()

            retry_config = RetryConfig(
                initial_delay=1.0,
                max_delay=30.0,
                multiplier=1.5,
                jitter=0.2
            )

            self.aws_manager = ResilientAWSManager(
                thing_name=self.aws_config['thing_name'],
                endpoint=self.aws_config['endpoint'],
                cert_path=str(self.aws_config['cert_path']),
                key_path=str(self.aws_config['key_path']),
                ca_path=str(self.aws_config['ca_path']),
                retry_config=retry_config,
                use_unique_client_id=True
            )

            self.aws_manager.connect()
            time.sleep(2)  # Wait for connection

            if self.aws_manager.connected:
                logger.info(f'AWS connected: {self.aws_config["thing_name"]}')
            else:
                logger.warning('AWS connection not established')

        except Exception as e:
            logger.error(f'Failed to initialize AWS: {e}')
            self.aws_manager = None

    def publish_status(self, connection_state: str, details: dict = None):
        """Publish status update to AWS IoT"""
        if not self.aws_manager or not self.aws_manager.connected:
            logger.warning('AWS not connected - status not published')
            return False

        try:
            status_payload = {
                "thingName": self.aws_config["thing_name"],
                "connectionState": connection_state,
                "deviceType": "raspberry-pi",
                "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
            }

            if details:
                status_payload.update(details)

            shadow_payload = {"state": {"reported": status_payload}}

            success = self.aws_manager.publish_with_retry(
                self.aws_manager.shadow_update_topic,
                shadow_payload
            )

            if success:
                logger.info(f'Status published: {connection_state}')
            else:
                logger.error(f'Failed to publish status: {connection_state}')

            return success

        except Exception as e:
            logger.error(f'Error publishing status: {e}')
            return False

    def stop_main_service(self):
        """Stop the tsv6.service"""
        try:
            logger.info('Stopping tsv6.service...')
            result = subprocess.run(
                ['sudo', 'systemctl', 'stop', 'tsv6.service'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info('tsv6.service stopped successfully')
            else:
                logger.warning(f'Failed to stop service: {result.stderr}')
        except Exception as e:
            logger.error(f'Error stopping service: {e}')

    def start_main_service(self):
        """Start the tsv6.service"""
        try:
            logger.info('Starting tsv6.service...')
            result = subprocess.run(
                ['sudo', 'systemctl', 'start', 'tsv6.service'],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                logger.info('tsv6.service started successfully')
            else:
                logger.warning(f'Failed to start service: {result.stderr}')
        except Exception as e:
            logger.error(f'Error starting service: {e}')

    def close_servo(self):
        """Close the servo door"""
        if self.servo_controller:
            try:
                logger.info('Closing servo...')
                self.servo_controller.close_door(hold_time=0.5)
                logger.info('Servo closed')
                return True
            except Exception as e:
                logger.error(f'Failed to close servo: {e}')
                return False
        return False

    def on_item_cleared(self):
        """Handle the 'Item Cleared' button press"""
        logger.info('Item Cleared button pressed')

        # Publish "Obstruction Clearing" status to AWS
        self.publish_status("Obstruction Clearing", {
            "obstruction": {
                "action": "user_clearing",
                "cleared_at": datetime.datetime.utcnow().isoformat() + "Z"
            }
        })

        # Update UI to show processing
        self.button.config(state=tk.DISABLED, text='Closing door...')
        self.root.update()

        # Close the servo
        success = self.close_servo()

        if success:
            self.button.config(text='Restarting service...')
            self.root.update()

            # Publish "Obstruction Cleared" status to AWS
            self.publish_status("Obstruction Cleared", {
                "obstruction": {
                    "action": "cleared",
                    "servo_closed": True,
                    "cleared_at": datetime.datetime.utcnow().isoformat() + "Z"
                }
            })

            time.sleep(1)

            # Cleanup servo
            if self.servo_controller:
                self.servo_controller.disable_servo()

            # Start the main service
            self.start_main_service()

            # Publish "Service Restarted" status to AWS
            self.publish_status("connected", {
                "obstruction": {
                    "action": "service_restarted",
                    "restarted_at": datetime.datetime.utcnow().isoformat() + "Z"
                }
            })

            # Disconnect AWS before closing
            if self.aws_manager:
                try:
                    self.aws_manager.disconnect()
                except:
                    pass

            # Close this UI
            time.sleep(0.5)
            self.root.quit()
            self.root.destroy()
        else:
            # Publish failure status
            self.publish_status("Obstruction Clear Failed", {
                "obstruction": {
                    "action": "clear_failed",
                    "servo_closed": False
                }
            })

            # Show error and allow retry
            self.button.config(
                state=tk.NORMAL,
                text='Retry - Item Cleared',
                bg='#FF6B6B'
            )

    def create_rounded_button(self, parent, text, command, **kwargs):
        """Create a pill-shaped button using a Canvas"""
        width = kwargs.get('width', 300)
        height = kwargs.get('height', 60)
        bg_color = kwargs.get('bg', '#4CAF50')
        fg_color = kwargs.get('fg', 'white')

        # Create frame to hold canvas
        frame = tk.Frame(parent, bg=kwargs.get('parent_bg', '#B71C1C'))

        canvas = tk.Canvas(
            frame,
            width=width,
            height=height,
            bg=kwargs.get('parent_bg', '#B71C1C'),
            highlightthickness=0
        )
        canvas.pack()

        # Draw pill shape (rounded rectangle)
        radius = height // 2

        # Draw the pill shape
        canvas.create_arc(0, 0, height, height, start=90, extent=180, fill=bg_color, outline=bg_color)
        canvas.create_arc(width-height, 0, width, height, start=270, extent=180, fill=bg_color, outline=bg_color)
        canvas.create_rectangle(radius, 0, width-radius, height, fill=bg_color, outline=bg_color)

        # Add text
        canvas.create_text(
            width//2, height//2,
            text=text,
            fill=fg_color,
            font=('Helvetica', 18, 'bold')
        )

        # Bind click event
        canvas.bind('<Button-1>', lambda e: command())

        # Store reference for updates
        canvas.bg_color = bg_color
        canvas.text_id = canvas.find_all()[-1]  # Last item is the text

        return frame, canvas

    def run(self):
        """Run the obstruction handler UI"""
        logger.info('Starting Obstruction Handler UI')

        # Stop the main service first
        self.stop_main_service()
        time.sleep(1)  # Give service time to stop

        # Create the main window
        self.root = tk.Tk()
        self.root.title('Device Obstruction')
        self.root.configure(bg='#B71C1C')  # Dark red background

        # Get screen dimensions first
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        logger.info(f'Screen dimensions: {screen_width}x{screen_height}')

        # Set window geometry to fill entire screen
        self.root.geometry(f'{screen_width}x{screen_height}+0+0')

        # Make fullscreen and topmost
        self.root.overrideredirect(True)  # Remove window decorations
        self.root.attributes('-topmost', True)

        # Lift window to front
        self.root.lift()
        self.root.focus_force()

        # Hide the mouse cursor
        self.root.config(cursor='none')

        # Create main container
        container = tk.Frame(self.root, bg='#B71C1C')
        container.place(relx=0.5, rely=0.5, anchor='center')

        # Warning icon (using text)
        warning_label = tk.Label(
            container,
            text='⚠',
            font=('Helvetica', 80),
            fg='#FFEB3B',  # Yellow warning
            bg='#B71C1C'
        )
        warning_label.pack(pady=(0, 20))

        # Main message
        title_label = tk.Label(
            container,
            text='Item Obstructed in Topper Stopper',
            font=('Helvetica', 28, 'bold'),
            fg='white',
            bg='#B71C1C',
            wraplength=screen_width - 100
        )
        title_label.pack(pady=(0, 30))

        # Subtitle
        subtitle_label = tk.Label(
            container,
            text='Door locked open. Please remove item.',
            font=('Helvetica', 20),
            fg='#FFCDD2',  # Light red/pink
            bg='#B71C1C',
            wraplength=screen_width - 100
        )
        subtitle_label.pack(pady=(0, 50))

        # Create pill-shaped button
        self.button = tk.Button(
            container,
            text='Item Cleared',
            font=('Helvetica', 20, 'bold'),
            fg='white',
            bg='#4CAF50',  # Green
            activebackground='#45a049',
            activeforeground='white',
            relief=tk.FLAT,
            padx=50,
            pady=15,
            cursor='none',  # Hide cursor on button too
            command=self.on_item_cleared
        )

        # Make button rounded using custom style
        self.button.config(
            borderwidth=0,
            highlightthickness=0
        )
        self.button.pack(pady=20)

        # Add subtle instruction
        hint_label = tk.Label(
            container,
            text='Press the button after removing the obstruction',
            font=('Helvetica', 12),
            fg='#EF9A9A',  # Lighter red
            bg='#B71C1C'
        )
        hint_label.pack(pady=(30, 0))

        # Bind escape key to close (for testing)
        self.root.bind('<Escape>', lambda e: self._emergency_exit())

        logger.info('UI ready, entering main loop')

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
        if self.servo_controller:
            try:
                self.servo_controller.disable_servo()
            except:
                pass


def main():
    """Main entry point"""
    print('=' * 60)
    print('  TSV6 Obstruction Handler Service')
    print('=' * 60)

    try:
        handler = ObstructionHandlerUI()
        handler.run()
    except Exception as e:
        logger.error(f'Obstruction handler failed: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print('Obstruction handler exited')


if __name__ == '__main__':
    main()
