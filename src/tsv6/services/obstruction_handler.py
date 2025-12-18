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
from typing import Tuple

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

    # Maximum clearing attempts before locking door open
    MAX_CLEAR_ATTEMPTS = 3

    def __init__(self):
        self.root = None
        self.servo_controller = None
        self.servo = None
        self.port_handler = None
        self.aws_manager = None
        self.aws_config = None
        self.error_label = None
        self.clear_attempts = 0  # Track clearing attempts
        self._setup_display()
        # Servo and AWS init deferred to run() for faster UI display

    def _setup_display(self):
        """Setup display environment"""
        if not os.environ.get('DISPLAY'):
            if os.path.exists('/tmp/.X11-unix/X0'):
                os.environ['DISPLAY'] = ':0'
                logger.info('DISPLAY set to :0')
            else:
                logger.error('No display available')
                sys.exit(1)

    def _init_servo_lightweight(self):
        """Initialize servo controller without moving to closed position"""
        try:
            # Add vendor path for servo SDK
            vendor_path = project_root / 'src/tsv6/hardware/stservo/vendor'
            if str(vendor_path) not in sys.path:
                sys.path.insert(0, str(vendor_path))

            from scservo_sdk import PortHandler, sms_sts, SMS_STS_TORQUE_ENABLE

            # Auto-detect port
            ports = ['/dev/ttyUSB0', '/dev/ttyUSB1', '/dev/ttyACM0', '/dev/ttyACM1']
            port = None
            for p in ports:
                if os.path.exists(p):
                    port = p
                    break

            if not port:
                logger.error('No servo port found')
                return

            # Connect without moving servo
            self.port_handler = PortHandler(port)
            self.port_handler.baudrate = 1000000

            if not self.port_handler.openPort():
                logger.error(f'Failed to open port {port}')
                return

            self.servo = sms_sts(self.port_handler)
            self.servo_id = 1
            self.SMS_STS_TORQUE_ENABLE = SMS_STS_TORQUE_ENABLE

            # Store positions for later
            self.closed_position = 4030
            self.open_position = 2868

            logger.info(f'Servo connected on {port} (lightweight init)')

        except Exception as e:
            logger.error(f'Failed to initialize servo: {e}')
            self.servo = None

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
            # Don't wait - connection happens in background

            logger.info(f'AWS connecting: {self.aws_config["thing_name"]}')

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

    def close_servo(self) -> Tuple[bool, str]:
        """
        Close the servo door (re-enables torque first).

        Returns:
            Tuple of (success, error_message) where error_message is empty on success
        """
        # Wait briefly for background init if servo not ready yet
        if not self.servo:
            logger.info('Waiting for servo initialization...')
            for _ in range(20):  # Wait up to 2 seconds
                time.sleep(0.1)
                if self.servo:
                    break

        # If still no servo, try to reinitialize (handles retry scenarios)
        if not self.servo:
            logger.info('Servo not ready, attempting to reinitialize...')
            self._init_servo_lightweight()
            time.sleep(0.5)  # Give it time to connect

        if not self.servo:
            logger.error('Servo not initialized after retry')
            return (False, 'Servo not connected')

        try:
            # Re-enable torque (was disabled after obstruction detected)
            logger.info('Re-enabling servo torque...')
            self.servo.write1ByteTxRx(self.servo_id, self.SMS_STS_TORQUE_ENABLE, 1)
            time.sleep(0.1)  # Small delay after torque enable

            # Read current position (for logging only - we don't check it)
            try:
                current_pos, _, _ = self.servo.ReadPos(self.servo_id)
                logger.info(f'Current position before close: {current_pos}')
            except:
                current_pos = None
                logger.warning('Could not read current position')

            logger.info(f'Closing servo to position {self.closed_position}...')
            # WritePosEx(id, position, speed, acceleration)
            # Always command to closed position regardless of current position
            self.servo.WritePosEx(self.servo_id, self.closed_position, 0, 50)

            # Wait for movement to complete with position verification
            max_wait = 2.0  # Maximum wait time
            poll_interval = 0.1
            waited = 0.0

            while waited < max_wait:
                time.sleep(poll_interval)
                waited += poll_interval

                try:
                    # Check if servo reached target position
                    pos, _, _ = self.servo.ReadPos(self.servo_id)
                    # Check if within tolerance (±50 units)
                    if abs(pos - self.closed_position) < 50:
                        logger.info(f'Servo closed successfully at position {pos}')
                        return (True, '')
                except Exception as e:
                    logger.warning(f'Could not read position during wait: {e}')

            # Timeout - check final position
            try:
                final_pos, _, _ = self.servo.ReadPos(self.servo_id)
                if abs(final_pos - self.closed_position) < 50:
                    logger.info(f'Servo closed at position {final_pos}')
                    return (True, '')
                else:
                    logger.error(f'Servo did not reach target: at {final_pos}, target {self.closed_position}')
                    return (False, f'Door stuck at position {final_pos}')
            except Exception as e:
                logger.error(f'Could not verify final position: {e}')
                # If we can't read position but command was sent, assume success
                logger.info('Command sent, assuming success')
                return (True, '')

        except Exception as e:
            logger.error(f'Failed to close servo: {e}')
            return (False, f'Servo error: {str(e)}')

    def open_fully_and_disable_torque(self) -> bool:
        """
        Move servo to full open position and disable torque.
        Called after max clearing attempts to allow manual item removal.

        Returns:
            True if successful, False otherwise
        """
        logger.info('Opening door fully and disabling torque (max attempts reached)')

        # Ensure servo is initialized
        if not self.servo:
            self._init_servo_lightweight()
            time.sleep(0.5)

        if not self.servo:
            logger.error('Cannot open door - servo not connected')
            return False

        try:
            # Enable torque first to move
            logger.info('Enabling torque to move to open position...')
            self.servo.write1ByteTxRx(self.servo_id, self.SMS_STS_TORQUE_ENABLE, 1)
            time.sleep(0.1)

            # Move to full open position
            open_pos = getattr(self, 'open_position', 2868)
            logger.info(f'Moving servo to full open position {open_pos}...')
            self.servo.WritePosEx(self.servo_id, open_pos, 0, 50)

            # Wait for movement to complete
            time.sleep(1.5)

            # Verify position (optional, just for logging)
            try:
                pos, _, _ = self.servo.ReadPos(self.servo_id)
                logger.info(f'Servo at position {pos} after open command')
            except:
                pass

            # Now disable torque so user can freely move the door
            logger.info('Disabling servo torque for manual removal...')
            self.servo.write1ByteTxRx(self.servo_id, self.SMS_STS_TORQUE_ENABLE, 0)

            logger.info('Door fully open, torque disabled')
            return True

        except Exception as e:
            logger.error(f'Failed to open door and disable torque: {e}')
            return False

    def on_item_cleared(self):
        """Handle the 'Item Cleared' button press"""
        self.clear_attempts += 1
        logger.info(f'Item Cleared button pressed (attempt {self.clear_attempts}/{self.MAX_CLEAR_ATTEMPTS})')

        # Publish "Obstruction Clearing" status to AWS
        self.publish_status("Obstruction Clearing", {
            "obstruction": {
                "action": "user_clearing",
                "attempt": self.clear_attempts,
                "cleared_at": datetime.datetime.utcnow().isoformat() + "Z"
            }
        })

        # Update UI to show processing
        self.button.config(state=tk.DISABLED, text='Closing door...')
        # Update error label if exists
        if hasattr(self, 'error_label') and self.error_label:
            self.error_label.config(text='')
        self.root.update()

        # Close the servo - returns (success, error_message)
        success, error_message = self.close_servo()

        if success:
            self.button.config(text='Restarting service...')
            self.root.update()

            # Publish "Obstruction Cleared" status to AWS
            self.publish_status("Obstruction Cleared", {
                "obstruction": {
                    "action": "cleared",
                    "servo_closed": True,
                    "attempts_needed": self.clear_attempts,
                    "cleared_at": datetime.datetime.utcnow().isoformat() + "Z"
                }
            })

            time.sleep(1)

            # Cleanup servo port
            if hasattr(self, 'port_handler') and self.port_handler:
                try:
                    self.port_handler.closePort()
                except:
                    pass

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
            # Check if max attempts reached
            if self.clear_attempts >= self.MAX_CLEAR_ATTEMPTS:
                # Max attempts reached - open door fully and disable torque
                logger.warning(f'Max clearing attempts ({self.MAX_CLEAR_ATTEMPTS}) reached, locking door open')

                self.button.config(text='Opening door...')
                self.root.update()

                # Open door fully and disable torque
                self.open_fully_and_disable_torque()

                # Publish "Door Locked Open" status to AWS
                self.publish_status("Door Locked Open", {
                    "obstruction": {
                        "action": "door_locked_open",
                        "servo_closed": False,
                        "torque_disabled": True,
                        "attempts": self.clear_attempts,
                        "error": error_message
                    }
                })

                # Update UI to show door is locked open
                if hasattr(self, 'error_label') and self.error_label:
                    self.error_label.config(
                        text='Door locked open. Torque disabled.\nPlease remove item and restart device.',
                        fg='#FFEB3B'
                    )

                # Disable button - no more retries, device needs restart
                self.button.config(
                    state=tk.DISABLED,
                    text='Restart Required',
                    bg='#757575'  # Gray
                )
                self.root.update()
                logger.error('Door locked open after max attempts - restart required')

            else:
                # Still have retries left
                remaining = self.MAX_CLEAR_ATTEMPTS - self.clear_attempts

                # Publish failure status with error details
                self.publish_status("Obstruction Clear Failed", {
                    "obstruction": {
                        "action": "clear_failed",
                        "servo_closed": False,
                        "attempt": self.clear_attempts,
                        "attempts_remaining": remaining,
                        "error": error_message
                    }
                })

                # Show error message on screen
                if hasattr(self, 'error_label') and self.error_label:
                    self.error_label.config(text=f'Error: {error_message}\n({remaining} attempts remaining)')

                # Show error and allow retry
                self.button.config(
                    state=tk.NORMAL,
                    text=f'Retry ({remaining} left)',
                    bg='#FF6B6B'
                )
                self.root.update()
                logger.warning(f'Close failed: {error_message}, {remaining} retries remaining')

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

    def _init_background(self):
        """Initialize servo and AWS in background after UI is shown"""
        import threading

        def init_task():
            self._init_servo_lightweight()
            self._init_aws()

        thread = threading.Thread(target=init_task, daemon=True)
        thread.start()

    def run(self):
        """Run the obstruction handler UI"""
        logger.info('Starting Obstruction Handler UI')

        # Stop the main service first (quick operation)
        self.stop_main_service()

        # Create the main window IMMEDIATELY - no delays
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

        # Error label (hidden initially, shown on failure)
        self.error_label = tk.Label(
            container,
            text='',
            font=('Helvetica', 14),
            fg='#FFEB3B',  # Yellow for visibility
            bg='#B71C1C',
            wraplength=screen_width - 100
        )
        self.error_label.pack(pady=(10, 0))

        # Add subtle instruction
        hint_label = tk.Label(
            container,
            text='Press the button after removing the obstruction',
            font=('Helvetica', 12),
            fg='#EF9A9A',  # Lighter red
            bg='#B71C1C'
        )
        hint_label.pack(pady=(20, 0))

        # Bind escape key to close (for testing)
        self.root.bind('<Escape>', lambda e: self._emergency_exit())

        logger.info('UI ready, entering main loop')

        # Schedule background initialization after UI is shown
        self.root.after(100, self._init_background)

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

        # Close lightweight servo port if used
        if hasattr(self, 'port_handler') and self.port_handler:
            try:
                self.port_handler.closePort()
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
