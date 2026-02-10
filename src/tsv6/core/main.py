#!/usr/bin/env python3
"""
Enhanced TSV6 Video Player with AWS IoT Integration and Optimized Barcode Scanning
Optimized for near-instant barcode to AWS IoT Core transmission using threading
"""

import vlc
import time
import sys
import tkinter as tk
import os
from pathlib import Path
import threading
import datetime
import math
import json
import uuid
import queue
from concurrent.futures import ThreadPoolExecutor

# Import PIL for image handling
try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("PIL (Pillow) not available. Install with: pip install Pillow")

# Import dotenv for environment variables
try:
    from dotenv import load_dotenv
    DOTENV_AVAILABLE = True
    load_dotenv()
except ImportError:
    DOTENV_AVAILABLE = False
    print("Dotenv library not available. Install with: pip install python-dotenv")

# Import OTA manager
try:
    from tsv6.ota.ota_manager import OTAManager
    OTA_MANAGER_AVAILABLE = True
    print("✓ OTA Manager imported successfully")
except ImportError as e:
    OTA_MANAGER_AVAILABLE = False
    print(f"⚠ OTA Manager not available: {e}")

# Import AWS manager and config
try:
    from tsv6.core.aws_resilient_manager import ResilientAWSManager, RetryConfig
    from tsv6.config.config import config
    AWS_MANAGER_AVAILABLE = True
    print("✓ AWS Manager imported successfully")
except ImportError as e:
    AWS_MANAGER_AVAILABLE = False
    print(f"⚠ AWS Manager not available: {e}")

# Import barcode reader
try:
    from tsv6.hardware.barcode_reader import BarcodeReader
    from tsv6.core.image_manager import ImageManager
    BARCODE_READER_AVAILABLE = True
    print("✓ Barcode Reader imported successfully")
except ImportError as e:
    BARCODE_READER_AVAILABLE = False
    print(f"⚠ Barcode Reader not available: {e}")

# Import AWS IoT SDK for MQTT QoS
try:
    from awscrt import mqtt
    AWS_CRT_AVAILABLE = True
except ImportError:
    AWS_CRT_AVAILABLE = False
    print("AWS CRT not available")

# Import memory optimizer for Issue #39
try:
    from tsv6.utils.memory_optimizer import get_global_memory_optimizer, optimize_memory_now
    MEMORY_OPTIMIZER_AVAILABLE = True
    print("✓ Memory Optimizer imported successfully")
except ImportError as e:
    MEMORY_OPTIMIZER_AVAILABLE = False
    print(f"⚠ Memory Optimizer not available: {e}")

# Import QR generator for NFC URL display
try:
    from tsv6.utils.qr_generator import generate_qr_code
    QR_GENERATOR_AVAILABLE = True
    print("✓ QR Generator imported successfully")
except ImportError as e:
    QR_GENERATOR_AVAILABLE = False
    print(f"⚠ QR Generator not available: {e}")

# Import NFC emulator for URL broadcasting
try:
    from tsv6.hardware.nfc import NFCEmulator
    NFC_EMULATOR_AVAILABLE = True
    print("✓ NFC Emulator imported successfully")
except ImportError as e:
    NFC_EMULATOR_AVAILABLE = False
    print(f"⚠ NFC Emulator not available: {e}")


class OptimizedBarcodeScanner:
    """Optimized barcode scanner with threading for instant AWS IoT transmission"""

    def __init__(self, aws_manager=None):
        self.running = False
        self.scan_thread = None
        self.publish_thread = None
        self.current_transaction_id = None

        # Barcode cooldown — prevent the same barcode from being processed twice rapidly
        self._last_barcode = None
        self._last_barcode_time = 0.0
        self._barcode_cooldown_secs = 10.0  # Ignore same barcode within 10 seconds

        # Thread-safe queue for barcode processing
        self.barcode_queue = queue.Queue(maxsize=100)

        # Thread pool for callbacks (REDUCED from 2 to 1 for lower memory usage)
        self.callback_executor = ThreadPoolExecutor(max_workers=1)

        # AWS Manager reference (use existing connection)
        self.aws_manager = aws_manager

        # Initialize barcode reader
        if BARCODE_READER_AVAILABLE:
            try:
                self.barcode_reader = BarcodeReader(quiet=True)
                print("✓ Barcode reader initialized")
            except Exception as e:
                print(f"Failed to initialize BarcodeReader: {e}")
                self.barcode_reader = None
        else:
            self.barcode_reader = None

        # Callback for video player
        self.barcode_callback = None

        # Callback to display processing image while AWS query is being processed
        self.processing_display_callback = None

        # Callback to handle QR code detection
        self.qr_code_callback = None

        # Memory optimization integration (Issue #39)
        if MEMORY_OPTIMIZER_AVAILABLE:
            self.memory_optimizer = get_global_memory_optimizer()
        else:
            self.memory_optimizer = None

        # NFC emulator for broadcasting URL with scanid after successful scan
        if NFC_EMULATOR_AVAILABLE:
            try:
                self.nfc_emulator = NFCEmulator(
                    base_url=os.getenv('NFC_BASE_URL', 'tsrewards--test.expo.app'),
                    timeout=10  # 10 second emulation timeout
                )
                self.nfc_emulator.on_tag_read = self._on_nfc_tag_read
                self.nfc_emulator.on_status_change = self._on_nfc_status_change
                print("✓ NFC Emulator initialized (10s broadcast timeout)")
            except Exception as e:
                print(f"Failed to initialize NFC Emulator: {e}")
                self.nfc_emulator = None
        else:
            self.nfc_emulator = None

    def _on_nfc_tag_read(self, scanid: str):
        """Callback when NFC tag is read by a phone"""
        print(f"📱 NFC tag read by phone! scanid: {scanid[:8]}...")

    def _on_nfc_status_change(self, status: str, scanid: str):
        """Callback for NFC emulation status changes"""
        if status == "started":
            print(f"📡 NFC broadcasting: https://tsrewards--test.expo.app?utm={scanid[:8]}...")
        elif status == "read":
            print(f"✅ NFC tag tapped! URL opened on phone")
        elif status == "timeout":
            print(f"⏱️ NFC broadcast timeout (10s) - no tap detected")
        elif status == "error":
            print(f"❌ NFC emulation error")

    def generate_uuid(self):
        """Generate a transaction ID"""
        return str(uuid.uuid4())
    
    def publish_to_aws_iot(self, barcode_data, transaction_id):
        """Publish barcode data to AWS IoT using direct MQTT connection"""
        try:
            # Get thing name from config or environment
            thing_name = config.device.thing_name if hasattr(config.device, 'thing_name') else os.getenv('AWS_IOT_THING_NAME', 'TSV6_RPI_DEVICE')

            # Use AWS Manager's direct MQTT connection if available
            if self.aws_manager and self.aws_manager.connected and self.aws_manager.connection:
                # Create shadow update topic
                pub_topic = f"$aws/things/{thing_name}/shadow/update"

                # Prepare the payload
                payload = {
                    "state": {
                        "reported": {
                            "thingName": thing_name,
                            "transactionID": transaction_id,
                            "barcode": barcode_data,
                            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
                            "deviceType": "raspberry-pi",
                            "scannerType": "USB-HID-KBW",
                            "application": "tsv6-videoplayer"
                        }
                    }
                }

                # Use AWS Manager's MQTT connection directly
                qos = mqtt.QoS.AT_LEAST_ONCE if AWS_CRT_AVAILABLE else 1
                publish_future, packet_id = self.aws_manager.connection.publish(
                    topic=pub_topic,
                    payload=json.dumps(payload),
                    qos=qos
                )

                # Show processing image immediately after publishing (async)
                if self.processing_display_callback:
                    try:
                        self.processing_display_callback()
                    except Exception as e:
                        print(f"⚠ Failed to display processing image: {e}")

                # Don't wait for result - let it happen async
                print(f"📡 Published to AWS IoT (async): {barcode_data}")
                print(f"   Topic: {pub_topic}")
                print(f"   Transaction: {transaction_id[:8]}...")
                return True

            else:
                print("❌ AWS Manager not connected")
                return False

        except Exception as e:
            print(f"❌ Failed to publish to AWS IoT: {e}")
            return False
    
    def scanner_worker(self):
        """Worker thread that continuously scans for barcodes"""
        print("🔍 Scanner worker thread started")
        
        while self.running:
            try:
                # Read barcode data
                barcode_data = None
                
                if self.barcode_reader:
                    try:
                        barcode_data = self.barcode_reader.scan_single()
                    except Exception as e:
                        print(f"Barcode read error: {e}")
                
                if barcode_data and barcode_data.strip():
                    barcode_data = barcode_data.strip()
                    transaction_id = self.generate_uuid()
                    timestamp = time.time()
                    
                    # Check if this is a QR code
                    is_qr = False
                    if self.barcode_reader and hasattr(self.barcode_reader, 'is_qr_code'):
                        is_qr = self.barcode_reader.is_qr_code(barcode_data)
                        
                        # If QR code detected, trigger callback but don't publish to AWS
                        if is_qr and self.qr_code_callback:
                            print(f"🔲 QR Code detected: {barcode_data}")
                            self.callback_executor.submit(
                                self.qr_code_callback,
                                barcode_data
                            )
                            # Skip putting QR codes in the queue for AWS publishing
                            continue

                    # Cooldown: ignore same barcode within cooldown window
                    now = time.time()
                    if (barcode_data == self._last_barcode and
                            (now - self._last_barcode_time) < self._barcode_cooldown_secs):
                        print(f"Duplicate barcode within {self._barcode_cooldown_secs}s cooldown, ignoring")
                        continue
                    self._last_barcode = barcode_data
                    self._last_barcode_time = now

                    # Stop any running NFC emulation (new scan supersedes previous)
                    if self.nfc_emulator and self.nfc_emulator.is_running():
                        print("Stopping previous NFC broadcast (new scan)")
                        self.nfc_emulator.stop_emulation()

                    # Put barcode (not QR) in queue immediately (non-blocking)
                    try:
                        self.barcode_queue.put_nowait({
                            'barcode': barcode_data,
                            'transaction_id': transaction_id,
                            'timestamp': timestamp
                        })
                        print(f"📱 Scanned: {barcode_data} → Queue (instant)")
                        
                        # Execute callback asynchronously
                        if self.barcode_callback:
                            self.callback_executor.submit(
                                self.barcode_callback, 
                                barcode_data, 
                                transaction_id
                            )
                    except queue.Full:
                        print("⚠ Barcode queue full, dropping scan")
                
                # Minimal sleep for rapid scanning
                time.sleep(0.001)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Scanner worker error: {e}")
                time.sleep(0.1)
        
        print("🔍 Scanner worker thread ended")
    
    def publisher_worker(self):
        """Worker thread that publishes barcodes to AWS IoT"""
        print("📤 Publisher worker thread started")
        
        while self.running or not self.barcode_queue.empty():
            try:
                # Get barcode from queue with timeout
                item = self.barcode_queue.get(timeout=0.1)
                
                # Calculate latency
                latency_ms = int((time.time() - item['timestamp']) * 1000)
                print(f"⚡ Processing barcode (queue latency: {latency_ms}ms)")
                
                # Publish to AWS IoT
                publish_start = time.time()
                success = self.publish_to_aws_iot(
                    item['barcode'], 
                    item['transaction_id']
                )
                publish_time_ms = int((time.time() - publish_start) * 1000)
                
                if success:
                    total_latency_ms = int((time.time() - item['timestamp']) * 1000)
                    print(f"✅ Transaction {item['transaction_id'][:8]}... sent")
                    print(f"   Total latency: {total_latency_ms}ms (queue: {latency_ms}ms, publish: {publish_time_ms}ms)")
                else:
                    print(f"❌ Failed to send transaction {item['transaction_id'][:8]}...")
                
                # Mark task as done
                self.barcode_queue.task_done()
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Publisher worker error: {e}")
        
        print("📤 Publisher worker thread ended")
    
    def start_scanning(self):
        """Start the barcode scanning with threaded workers"""
        if self.running:
            return
        
        self.running = True
        
        # Start scanner thread
        self.scan_thread = threading.Thread(target=self.scanner_worker, name="Scanner")
        self.scan_thread.daemon = True
        self.scan_thread.start()
        
        # Start publisher thread
        self.publish_thread = threading.Thread(target=self.publisher_worker, name="Publisher")
        self.publish_thread.daemon = True
        self.publish_thread.start()
        
        print("🚀 Optimized barcode scanning started (threaded)")
        print("   Scanner → Queue → Publisher (all non-blocking)")
    
    def stop_scanning(self):
        """Stop the barcode scanning"""
        print("🛑 Stopping barcode scanner...")
        self.running = False

        # Stop NFC emulation if running
        if self.nfc_emulator:
            self.nfc_emulator.stop_emulation()

        # Wait for threads to finish
        if self.scan_thread:
            self.scan_thread.join(timeout=2)
        if self.publish_thread:
            self.publish_thread.join(timeout=2)
        
        # Shutdown callback executor
        self.callback_executor.shutdown(wait=False)
        
        # Perform memory cleanup (Issue #39)
        self.cleanup_memory()
        
        print("🛑 Barcode scanning stopped")
    
    def cleanup_memory(self):
        """Clean up memory resources and optimize usage (Issue #39)"""
        try:
            # Clear the barcode queue
            while not self.barcode_queue.empty():
                try:
                    self.barcode_queue.get_nowait()
                    self.barcode_queue.task_done()
                except:
                    break
            
            # Force garbage collection
            import gc
            gc.collect()
            
            # Trigger memory optimization if available
            if self.memory_optimizer:
                result = self.memory_optimizer.optimize_memory_usage(force=True)
                if result.get('optimized'):
                    print(f"🧠 Memory optimized: saved {result.get('memory_saved_mb', 0):.1f} MB")
            
        except Exception as e:
            print(f"⚠ Memory cleanup error: {e}")


class EnhancedVideoPlayer:
    """Enhanced video player with optimized barcode scanning integration"""
    
    def __init__(self, aws_manager=None, memory_optimizer=None):
        # Initialize optimized VLC instance with hardware acceleration and caching
        self.instance = self._create_vlc_instance()
        
        # Create MediaListPlayer for seamless video transitions (eliminates GPU reinit)
        if config.video.use_medialist_player:
            self.media_list = self.instance.media_list_new()
            self.list_player = self.instance.media_list_player_new()
            self.player = self.instance.media_player_new()
            self.list_player.set_media_player(self.player)
            print("✓ MediaListPlayer initialized (optimized playlist mode)")
        else:
            # Fallback to traditional player
            self.player = self.instance.media_player_new()
            self.media_list = None
            self.list_player = None
            print("✓ Traditional MediaPlayer initialized")
        
        self.root = None
        self.canvas = None
        self.current_video_index = 0
        self.video_files = []
        self.is_playing = False
        self.status_publish_active = False
        self.aws_manager = aws_manager
        self._owns_aws_manager = (aws_manager is None)
        self.ota_manager = None
        
        # Memory optimizer (can be injected or use global singleton)
        if memory_optimizer is not None:
            # Use injected optimizer (from production_main.py)
            self.memory_optimizer = memory_optimizer
        elif MEMORY_OPTIMIZER_AVAILABLE:
            # Fallback to global singleton
            self.memory_optimizer = get_global_memory_optimizer()
        else:
            self.memory_optimizer = None
        
        # Image display components (NEW)
        self.image_manager = ImageManager()
        self.image_overlay = None
        self.image_display_timer = None
        self.is_showing_image = False
        self.video_was_playing = False
        self.media_cache = {}
        self.current_media_path = None
        self.video_surface_bound = False
        self.max_cache_size = 5  # Limit media cache size for memory management

        # Processing image overlay (for AWS query verification)
        self.processing_overlay = None
        self.processing_display_timer = None
        
        # Initialize AWS Manager first only if not injected
        if self.aws_manager is None and AWS_MANAGER_AVAILABLE:
            self.initialize_aws_manager()
        
        # Initialize optimized barcode scanner with AWS manager
        self.barcode_scanner = OptimizedBarcodeScanner(aws_manager=self.aws_manager)

        # Set processing display callback for showing verification image during AWS query
        self.barcode_scanner.processing_display_callback = self.display_processing_image

        # Set QR code detection callback
        self.barcode_scanner.qr_code_callback = self.display_qr_not_allowed_image

        # Connect AWS message handler callbacks only if we own the manager
        if self.aws_manager and self._owns_aws_manager:
            self.aws_manager.set_image_display_callback(self.display_product_image)
            self.aws_manager.set_no_match_display_callback(self.display_no_match_image)
            print("DEBUG: No match callback registered successfully!")
    
    def _create_vlc_instance(self):
        """Create optimized VLC instance with hardware acceleration and caching"""
        vlc_args = [
            # Audio disabled
            '--aout=dummy',
            '--no-audio',
            
            # File caching - 2 seconds for SD card latency (Raspberry Pi specific)
            f'--file-caching={config.video.file_caching_ms}',
            
            # Network caching (minimal since local files)
            f'--network-caching={config.video.network_caching_ms}',
        ]
        
        # Hardware acceleration (with fallback support)
        if config.video.hardware_acceleration:
            vlc_args.append('--avcodec-hw=any')
        
        # Video output mode
        vlc_args.append(f'--vout={config.video.vout_mode}')
        
        # Optional optimization flags
        if config.video.disable_video_title:
            vlc_args.append('--no-video-title-show')
        if config.video.disable_stats:
            vlc_args.append('--no-stats')
        if config.video.disable_snapshot_preview:
            vlc_args.append('--no-snapshot-preview')
        if config.video.disable_screensaver:
            vlc_args.append('--no-disable-screensaver')
        
        # Clock optimization
        vlc_args.extend([
            f'--clock-jitter={config.video.clock_jitter}',
            f'--clock-synchro={config.video.clock_synchro}',
        ])
        
        # Additional performance flags
        vlc_args.extend([
            '--no-sout-keep',
            '--no-sub-autodetect-file',
        ])
        
        try:
            instance = vlc.Instance(' '.join(vlc_args))
            print(f"✓ VLC Instance created with optimizations:")
            print(f"  - File caching: {config.video.file_caching_ms}ms")
            print(f"  - Hardware acceleration: {config.video.hardware_acceleration}")
            print(f"  - Video output: {config.video.vout_mode}")
            return instance
        except Exception as e:
            # Fallback to software decoding if hardware acceleration fails
            if config.video.hardware_acceleration and config.video.hardware_acceleration_fallback:
                print(f"⚠ Hardware acceleration failed, falling back to software: {e}")
                vlc_args = [arg for arg in vlc_args if not arg.startswith('--avcodec-hw')]
                instance = vlc.Instance(' '.join(vlc_args))
                print("✓ VLC Instance created with software decoding")
                return instance
            raise
    
    def initialize_aws_manager(self):
        """Initialize AWS manager for status publishing"""
        if not AWS_MANAGER_AVAILABLE:
            print("AWS Manager not available")
            return
        
        try:
            # Get certificate paths from config
            cert_path = str(config.files.CERTS_DIR / "aws_cert_crt.pem")
            key_path = str(config.files.CERTS_DIR / "aws_cert_private.pem")
            ca_path = str(config.files.CERTS_DIR / "aws_cert_ca.pem")
            
            print("🔧 Initializing AWS Manager...")
            print(f"  Thing name: {config.device.thing_name}")
            print(f"  Endpoint: {config.aws.IOT_ENDPOINT}")

            # Initialize Resilient AWS Manager
            retry_config = RetryConfig(
                max_retries=5,
                initial_backoff=1.0,
                max_backoff=30.0,
                backoff_multiplier=2.0
            )

            self.aws_manager = ResilientAWSManager(
                thing_name=config.device.thing_name,
                endpoint=config.aws.IOT_ENDPOINT,
                cert_path=cert_path,
                key_path=key_path,
                ca_path=ca_path,
                retry_config=retry_config,
                use_unique_client_id=True  # Enable unique client IDs to prevent DUPLICATE_CLIENTID errors
            )

            # Test connection
            if self.aws_manager.connect():
                print("✅ AWS Manager connected successfully")
            else:
                print("❌ AWS Manager connection failed")
                self.aws_manager = None

        except Exception as e:
            print(f"⚠ AWS Manager initialization failed: {e}")
            self.aws_manager = None
    
    def start_status_publishing(self):
        """Start AWS status publishing thread"""
        # Diagnostic logging to identify issues
        print("🔍 start_status_publishing() called")
        print(f"   aws_manager: {self.aws_manager is not None}")
        print(f"   status_publish_active: {self.status_publish_active}")
        
        if not self.aws_manager:
            print("❌ Cannot start status publishing: aws_manager is None")
            return
        
        if self.status_publish_active:
            print("⚠️  Status publishing already active, skipping")
            return
        
        print("✅ Starting status publisher thread...")
        self.status_publish_active = True
        
        def publish_status():
            """Background task to publish status every 60 seconds"""
            print("📡 Starting AWS status publishing...")
            consecutive_failures = 0
            publish_count = 0
            
            while self.status_publish_active and self.aws_manager:
                try:
                    # Publish device status
                    success = self.aws_manager.publish_status()
                    if success:
                        publish_count += 1
                        print(f"✅ Device status published to AWS (#{publish_count})")
                        consecutive_failures = 0
                    else:
                        consecutive_failures += 1
                        print(f"⚠️  Failed to publish device status (attempt {consecutive_failures})")
                        
                    if consecutive_failures >= 5:
                        print(f"❌ Status publishing failed {consecutive_failures} consecutive times")
                        
                except Exception as e:
                    consecutive_failures += 1
                    print(f"❌ Status publish error (attempt {consecutive_failures}): {type(e).__name__}: {e}")
                    import traceback
                    traceback.print_exc()
                
                # Wait 60 seconds before next publish
                for _ in range(600):  # 60 seconds in 0.1s increments
                    if not self.status_publish_active:
                        break
                    time.sleep(0.1)
            
            print(f"📡 AWS status publishing stopped (published {publish_count} times)")
        
        # Start status publishing thread
        status_thread = threading.Thread(target=publish_status, name="StatusPublisher")
        status_thread.daemon = True
        status_thread.start()
        
        # Verify thread started
        time.sleep(0.5)
        thread_found = any(t.name == "StatusPublisher" for t in threading.enumerate())
        if thread_found:
            print("✅ StatusPublisher thread verified running")
        else:
            print("❌ WARNING: StatusPublisher thread failed to start!")

    
    def detect_and_set_display(self):
        """Simple display detection for Waveshare DSI screen"""
        if os.environ.get('DISPLAY'):
            return True
        
        # Quick check for X11 display
        if os.path.exists('/tmp/.X11-unix/X0'):
            os.environ['DISPLAY'] = ':0'
            print('✓ DISPLAY set to :0')
            return True
        
        return False

    def setup_video_display(self):
        """Set up the video display window with bottom button area"""
        if not self.detect_and_set_display():
            print("❌ No display - exiting")
            return
        
        print("🖥️ Setting up video display for Waveshare 7-inch DSI screen...")
        self.root = tk.Tk()
        self.root.title("TSV6 Enhanced Video Player with Optimized Barcode Scanning")
        self.root.configure(bg='white')
        
        # Get screen dimensions first before going fullscreen
        self.root.update_idletasks()
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        
        # Set window geometry to match screen size and position at 0,0
        self.root.geometry(f"{screen_width}x{screen_height}+0+0")
        
        # Ensure window is on top and focused
        self.root.lift()
        self.root.focus_force()
        
        # Update to apply geometry changes
        self.root.update_idletasks()
        
        # Now make it fullscreen
        # Additional window manager hints for proper positioning
        self.root.attributes('-topmost', True)  # Keep window on top
        self.root.overrideredirect(True)  # Remove window decorations
        
        self.root.attributes('-fullscreen', True)
        self.root.config(cursor="none")
        
        # Force another update to ensure proper positioning
        self.root.update()
        print(f"📏 Detected screen resolution: {screen_width}x{screen_height}")
        
        # Calculate zone dimensions for 800x480 display
        upper_height = int(screen_height * 0.8125)
        lower_height = int(screen_height * 0.1875)
        video_width = screen_width

        # Upper frame for video
        upper_frame = tk.Frame(self.root, bg='white', width=screen_width, height=upper_height)
        upper_frame.pack_propagate(False)
        upper_frame.pack(side='top', fill='x')

        # Video frame
        self.video_frame = tk.Frame(upper_frame, bg='white', width=video_width, height=upper_height)
        self.video_frame.pack_propagate(False)
        self.video_frame.pack(fill='both', expand=True)

        # Lower frame (button zone)
        self.lower_frame = tk.Frame(self.root, bg='red', width=screen_width, height=lower_height)
        self.lower_frame.pack_propagate(False)
        self.lower_frame.pack(side='bottom', fill='x')

        # Add the large recycle button
        self.setup_recycle_button()
        
        # Set up the video canvas
        self.canvas = tk.Canvas(self.video_frame, bg='white', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self._ensure_video_surface()
        
        # Bind events
        self.root.bind('<Escape>', self.on_escape)
        self.root.bind('<space>', self.toggle_play_pause)
        self.root.bind('<Right>', self.next_video)
        self.root.bind('<Left>', self.previous_video)
        
        print("✅ Video display setup complete with recycle button")

    def _ensure_video_surface(self):
        """Bind VLC video output to the Tk canvas once"""
        if self.video_surface_bound or not self.player or not self.canvas:
            return

        try:
            if self.root:
                self.root.update_idletasks()
            window_id = self.canvas.winfo_id()
        except Exception:
            return

        if not window_id:
            return

        if sys.platform.startswith('linux'):
            self.player.set_xwindow(window_id)
        elif sys.platform == "win32":
            self.player.set_hwnd(window_id)
        elif sys.platform == "darwin":
            self.player.set_nsobject(window_id)

        self.video_surface_bound = True

    def setup_recycle_button(self):
        """Setup the large recycle button at the bottom"""
        # Calculate appropriate font size based on screen dimensions
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()

        # Calculate button area height
        button_area_height = int(screen_height * 0.1875)

        # Calculate font size
        button_width = screen_width
        button_height = button_area_height

        # Font size calculation
        # Account for the bounce animation which adds up to 10 pixels
        bounce_max_addition = 10
        
        # Maximum width should be 760px (800 - 20px padding on each side)
        # Reduced from 780 to give more margin
        max_text_width = 760
        
        # Calculate based on text length and character width
        text_length = len("SCAN ITEM TO RECYCLE")
        
        # More conservative character width ratio for bold fonts
        # Increased to account for actual bold font rendering
        avg_char_width_ratio = 0.7  # More conservative for safety
        
        # Calculate maximum font size that would fit within 760px at largest pulsation
        # We need to account for the bounce animation adding pixels
        max_font_size_for_width = int((max_text_width / text_length) / avg_char_width_ratio) - bounce_max_addition
        
        # Also constrain by height - reduced multiplier
        available_height = button_height * 0.7  # Reduced from 0.8
        max_font_size_for_height = int(available_height) - bounce_max_addition
        
        # Use the smaller of the two constraints
        font_size = min(max_font_size_for_width, max_font_size_for_height)
        
        # Constrain to reasonable limits - further reduced
        font_size = max(16, min(font_size, 36))  # Reduced max from 48 to 36
        
        # Store base font size
        self.base_font_size = font_size

        # Create button font
        try:
            button_font = ("Anton", font_size, "bold")
            tk.Label(self.root, font=button_font).destroy()
        except tk.TclError:
            button_font = ("Arial", font_size, "bold")
            print("Anton font not available, using system default")
        
        self.recycle_button = tk.Button(
            self.lower_frame,
            text="SCAN ITEM TO RECYCLE",
            command=self.recycle_action,
            bg="red",
            fg="white",
            activebackground="red",
            activeforeground="white",
            font=button_font,
            height=1,
            relief="flat",
            borderwidth=0,
            highlightthickness=0
        )
        self.recycle_button.pack(fill="both", expand=True, padx=5, pady=5)
        
        # Start bounce animation
        self.start_bounce_animation()
    
    def start_bounce_animation(self):
        """Start the gentle bounce animation for the button text"""
        self.bounce_animation_running = True
        self.bounce_counter = 0
        self.animate_bounce()
    
    def animate_bounce(self):
        """Animate the button text with a gentle bounce effect"""
        if not self.bounce_animation_running:
            return
            
        try:
            if not self.recycle_button.winfo_exists():
                return
                
            # Calculate bounce offset
            bounce_range = 8
            bounce_speed = 0.15
            
            # Use sine wave for smooth bounce
            bounce_offset = int(bounce_range * math.sin(self.bounce_counter * bounce_speed))
            
            # Apply bounce
            font_size_offset = max(0, bounce_offset)
            current_font_size = self.base_font_size + font_size_offset
            
            # Update font
            current_font = self.recycle_button.cget("font")
            if isinstance(current_font, tuple):
                font_family = current_font[0]
                font_weight = current_font[2] if len(current_font) > 2 else "bold"
            else:
                font_family = "Arial"
                font_weight = "bold"
            
            new_font = (font_family, current_font_size, font_weight)
            self.recycle_button.config(font=new_font)
            
            # Increment counter
            self.bounce_counter += 1
            if self.bounce_counter > 200:
                self.bounce_counter = 0
            
            # Schedule next frame
            if self.bounce_animation_running:
                self.root.after(80, self.animate_bounce)
                
        except Exception:
            pass
    
    def stop_bounce_animation(self):
        """Stop the bounce animation"""
        self.bounce_animation_running = False
    
    def recycle_action(self):
        """Action when the recycle button is pressed"""
        print("♻️ Recycling button pressed - handling recycle request")
    
    def load_videos(self, video_directory="assets/videos"):
        """Load video files from directory"""
        if not os.path.exists(video_directory):
            print(f"⚠ Video directory '{video_directory}' not found")
            return
        
        # Collect video files
        video_extensions = ('.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm')
        self.video_files = []
        
        for file in sorted(os.listdir(video_directory)):
            if file.lower().endswith(video_extensions):
                self.video_files.append(os.path.join(video_directory, file))
        
        if not self.video_files:
            print("⚠ No video files found")
            return
        
        # Use MediaListPlayer for optimized playback
        if config.video.use_medialist_player and self.list_player:
            # Clear existing media list
            self.media_list = self.instance.media_list_new()
            
            # Add all videos to media list
            for video_path in self.video_files:
                media = self.instance.media_new(video_path)
                self.media_list.add_media(media)
            
            # Set playlist to list player
            self.list_player.set_media_list(self.media_list)
            
            # Set loop mode for continuous playback
            self.list_player.set_playback_mode(vlc.PlaybackMode.loop)
            
            # Preload first video to warm up decoder/GPU pipeline
            if config.video.preload_decoder and self.video_files:
                print("🔥 Preloading decoder pipeline...")
                self.list_player.play_item_at_index(0)
                time.sleep(0.5)  # Allow decoder initialization
                self.list_player.pause()
                print("✓ Decoder pipeline preloaded (GPU warm)")
            
            print(f"🎬 Loaded {len(self.video_files)} videos into MediaListPlayer")
        else:
            # Traditional mode with media cache
            if self.media_cache:
                for media in self.media_cache.values():
                    try:
                        media.release()
                    except Exception:
                        pass
                self.media_cache.clear()
            self.current_media_path = None
            
            print(f"🎬 Loaded {len(self.video_files)} video files (traditional mode)")
    
    def _get_media(self, media_path):
        """Return cached VLC media object for the given path with memory management"""
        media = self.media_cache.get(media_path)
        if media is None:
            # Cleanup old cached media if cache is full
            if len(self.media_cache) >= self.max_cache_size:
                self._cleanup_media_cache()
            
            media = self.instance.media_new(media_path)
            self.media_cache[media_path] = media
        return media
    
    def _cleanup_media_cache(self):
        """Clean up half of the oldest cached media to free memory"""
        try:
            # Remove oldest half of cached media
            items_to_remove = list(self.media_cache.keys())[:len(self.media_cache)//2]
            for key in items_to_remove:
                if key != self.current_media_path:  # Don't remove current media
                    media = self.media_cache.pop(key, None)
                    if media:
                        media.release()
            print(f"🧹 Cleaned {len(items_to_remove)} cached media objects")
        except Exception as e:
            print(f"⚠ Media cache cleanup error: {e}")
    
    def cleanup_resources(self):
        """PHASE 3: Aggressive VLC resource cleanup for memory pressure
        
        This method is called by the memory optimizer when memory pressure is detected.
        It performs more aggressive cleanup than normal operation.
        """
        try:
            print("🧹 PHASE 3: Aggressive VLC cleanup triggered")
            
            # 1. Clear all media cache except current video
            if self.media_cache:
                items_removed = 0
                for path, media in list(self.media_cache.items()):
                    if path != self.current_media_path:
                        try:
                            media.release()
                            self.media_cache.pop(path, None)
                            items_removed += 1
                        except Exception as e:
                            print(f"⚠ Failed to release media {path}: {e}")
                
                if items_removed > 0:
                    print(f"   ✓ Released {items_removed} cached VLC media objects")
            
            # 2. Force VLC to release decoded frames (if stopped)
            if self.player:
                try:
                    # If video is stopped, clear the frame buffer
                    state = self.player.get_state()
                    if state in [vlc.State.Stopped, vlc.State.Ended]:
                        # Temporarily set to None to force frame release
                        current_media = self.player.get_media()
                        self.player.set_media(None)
                        # Restore media
                        if current_media:
                            self.player.set_media(current_media)
                        print("   ✓ VLC frame buffer cleared")
                except Exception as e:
                    print(f"⚠ VLC frame buffer cleanup failed: {e}")
            
            # 3. Clear image manager cache
            if hasattr(self, 'image_manager') and self.image_manager:
                try:
                    # Image manager has internal cache - trigger cleanup
                    if hasattr(self.image_manager, 'clear_cache'):
                        self.image_manager.clear_cache()
                        print("   ✓ Image cache cleared")
                except Exception as e:
                    print(f"⚠ Image cache cleanup failed: {e}")
            
            # 4. Reduce max cache size under memory pressure
            old_max = self.max_cache_size
            if self.memory_optimizer:
                status = self.memory_optimizer.get_memory_status()
                if status.alert_level in ["critical", "emergency"]:
                    self.max_cache_size = 2  # Reduce to 2 under pressure
                    print(f"   ✓ Reduced cache size: {old_max} → {self.max_cache_size}")
            
            print("✅ PHASE 3: Aggressive VLC cleanup completed")
            
        except Exception as e:
            print(f"❌ PHASE 3: Cleanup failed: {e}")
            import traceback
            traceback.print_exc()

    def _rewind_current_media(self):
        """Rewind currently loaded media without recreating decoder"""
        if not self.player or not self.current_media_path:
            return

        try:
            self.player.set_pause(True)
        except Exception:
            pass

        try:
            self.player.set_time(0)
        except Exception:
            try:
                self.player.set_position(0.0)
            except Exception:
                pass

        try:
            self.player.set_pause(False)
        except Exception:
            pass

    def play_current_video(self, restart=False):
        """Play the current video"""
        if not self.video_files:
            print("❌ No videos to play")
            return
        
        current_video = self.video_files[self.current_video_index]
        print(f"▶️ Playing: {os.path.basename(current_video)}")
        
        # Ensure VLC is bound to the canvas only once
        self._ensure_video_surface()
        
        # Use MediaListPlayer for optimized playback
        if config.video.use_medialist_player and self.list_player:
            if restart:
                # Jump to current index and restart
                self.list_player.play_item_at_index(self.current_video_index)
            else:
                # Continue playing from current state
                if self.list_player.is_playing() == 0:  # Not playing
                    self.list_player.play()
        else:
            # Traditional mode
            media = self._get_media(current_video)

            if self.current_media_path != current_video:
                self.player.stop()
                self.player.set_media(media)
                self.current_media_path = current_video
            elif restart or self.player.get_state() == vlc.State.Ended:
                self._rewind_current_media()
            
            # Play video
            self.player.play()
        
        self.is_playing = True
        
        # Schedule next video check with optimized interval
        check_interval = config.video.video_status_check_interval_ms if hasattr(config, 'video') else 2000
        self.root.after(check_interval, self.check_video_status)
    
    def check_video_status(self):
        """Check video status and handle end of video.

        Simplified version that:
        - Returns immediately if not playing (overlay handling)
        - Only handles Ended/Error states (not Stopped)
        - Only reschedules in the else branch (no multiple loops)
        - Trusts MediaListPlayer's native loop mode
        """
        # If not playing (e.g., overlay is showing), exit without rescheduling.
        # The overlay hide logic will restart the check loop when resuming.
        if not self.is_playing:
            return

        try:
            state = self.player.get_state()

            if state == vlc.State.Ended:
                print("🔄 Video ended, playing next...")
                self.next_video()
                # Don't reschedule - next_video -> play_current_video schedules it
            elif state == vlc.State.Error:
                print("❌ Video error, skipping to next...")
                self.next_video()
                # Don't reschedule - next_video -> play_current_video schedules it
            else:
                # Only reschedule when video is playing normally
                check_interval = config.video.video_status_check_interval_ms if hasattr(config, 'video') else 2000
                self.root.after(check_interval, self.check_video_status)
        except Exception as e:
            print(f"⚠️ Error checking video status: {e}")
            # On error, reschedule to keep monitoring
            self.root.after(2000, self.check_video_status)
    
    def next_video(self, event=None):
        """Play next video in sequence with proper cleanup"""
        if not self.video_files:
            return
        
        # Use MediaListPlayer for seamless transitions (no cleanup needed)
        if config.video.use_medialist_player and self.list_player:
            self.list_player.next()
            self.current_video_index = (self.current_video_index + 1) % len(self.video_files)
            print(f"⏭️  Next video: {os.path.basename(self.video_files[self.current_video_index])}")
        else:
            # Traditional mode with cleanup
            if MEMORY_OPTIMIZER_AVAILABLE:
                optimize_memory_now()
            else:
                import gc
                gc.collect()
            
            # Cleanup current media resources (keep player for efficiency)
            self._cleanup_current_media()
            
            self.current_video_index = (self.current_video_index + 1) % len(self.video_files)
            self.play_current_video()
    
    def previous_video(self, event=None):
        """Play previous video in sequence"""
        if not self.video_files:
            return
        
        # Use MediaListPlayer for seamless transitions
        if config.video.use_medialist_player and self.list_player:
            self.list_player.previous()
            self.current_video_index = (self.current_video_index - 1) % len(self.video_files)
            print(f"⏮️  Previous video: {os.path.basename(self.video_files[self.current_video_index])}")
        else:
            # Traditional mode
            self.current_video_index = (self.current_video_index - 1) % len(self.video_files)
            self.play_current_video()
    
    def toggle_play_pause(self, event=None):
        """Toggle play/pause"""
        if self.is_playing:
            # Use MediaListPlayer if available
            if config.video.use_medialist_player and self.list_player:
                self.list_player.pause()
            elif self.player:
                self.player.pause()
            print("⏸️ Video paused")
        else:
            # Use MediaListPlayer if available
            if config.video.use_medialist_player and self.list_player:
                self.list_player.play()
            elif self.player:
                self.player.play()
            print("▶️ Video resumed")
        self.is_playing = not self.is_playing
    
    def on_escape(self, event=None):
        """Handle escape key"""
        self.cleanup_and_exit()
    

    def display_product_image(self, product_data):
        """
        Display product image when openDoor message is received

        Args:
            product_data: Dict with productImage, productName, productBrand, nfcUrl, etc.
        """
        image_url = product_data.get('productImage')
        product_name = product_data.get('productName', 'Product')
        product_brand = product_data.get('productBrand', '')
        barcode = product_data.get('barcode', '')
        nfc_url = product_data.get('nfcUrl', '')

        if not image_url:
            print("⚠ No product image URL provided")
            return

        # Hide processing image if it's still showing
        self._hide_processing_overlay()

        print(f"🖼️ Displaying product image: {product_name}")
        if nfc_url:
            print(f"📱 NFC URL available for QR code: {nfc_url[:50]}...")

        def image_ready_callback(image_path, success):
            """Called when image download is complete"""
            if success and image_path and self.root:
                # Schedule image display on main thread
                self.root.after(0, lambda: self._show_image_overlay(image_path, product_name, product_brand, barcode, nfc_url))
            else:
                print("❌ Failed to download product image")

        # Start image download
        self.image_manager.download_image(image_url, image_ready_callback)

    def start_nfc_for_transaction(self, nfc_url: str, transaction_id: str = ""):
        """
        Start NFC URL broadcasting for a transaction.

        Called by production_main.py after the servo door has closed.
        Broadcasts the provided URL for 10 seconds or until next scan.

        Args:
            nfc_url: The complete URL to broadcast via NFC
            transaction_id: Optional transaction ID for logging
        """
        if not nfc_url:
            print("⚠ No NFC URL provided")
            return

        if self.barcode_scanner and self.barcode_scanner.nfc_emulator:
            try:
                self.barcode_scanner.nfc_emulator.start_emulation_with_url(nfc_url, transaction_id)
                display_id = transaction_id[:8] if transaction_id else "N/A"
                print(f"📡 NFC broadcasting URL: {nfc_url[:50]}... (txn: {display_id})")
            except Exception as e:
                print(f"⚠ NFC emulation failed to start: {e}")
        else:
            print("⚠ NFC emulator not available")

    def display_no_match_image(self):
        """
        Display no match image when noMatch message is received from AWS

        This method displays the cannot_accept.jpg from event_images folder for 5 seconds
        then restarts video playback.
        """
        print(f"❌ Displaying no match image")
        print(f"🔍 DEBUG: display_no_match_image() called successfully!")
        print(f"🔍 DEBUG: self.root exists: {self.root is not None}")

        # Hide processing image if it's still showing
        self._hide_processing_overlay()

        # Get the path to the no match image
        no_match_image_path = os.path.join('event_images', 'cannot_accept.jpg')

        if not os.path.exists(no_match_image_path):
            print(f"⚠ No match image not found at: {no_match_image_path}")
            return

        if self.root:
            # Schedule image display on main thread with 5 second duration
            self.root.after(0, self._show_no_match_overlay, no_match_image_path)

    def display_qr_not_allowed_image(self):
        """
        Display barcode_not_qr.jpg image when a QR code is scanned

        This method displays the barcode_not_qr.jpg from event_images folder for 5 seconds
        then restarts video playback.
        """
        print(f"🔲 Displaying QR code not allowed image")

        # Hide processing image if it's still showing
        self._hide_processing_overlay()

        # Get the path to the QR code not allowed image
        qr_not_allowed_image_path = os.path.join('event_images', 'barcode_not_qr.jpg')

        if not os.path.exists(qr_not_allowed_image_path):
            print(f"⚠ QR not allowed image not found at: {qr_not_allowed_image_path}")
            return

        if self.root:
            # Schedule image display on main thread with 5 second duration
            self.root.after(0, self._show_qr_not_allowed_overlay, qr_not_allowed_image_path)

    def display_processing_image(self):
        """
        Display processing/verification image while AWS Lambda query is being processed

        Shows image_verify.jpg from event_images folder. This persists until either
        the product image is displayed (success) or no-match image is displayed.
        """
        if not self.root:
            return

        # Get the path to the processing image
        processing_image_path = os.path.join('event_images', 'image_verify.jpg')

        if not os.path.exists(processing_image_path):
            print(f"⚠ Processing image not found at: {processing_image_path}")
            return

        print(f"⏳ Displaying processing/verification image")

        # Schedule image display on main thread
        self.root.after(0, self._show_processing_overlay, processing_image_path)
    
    def _pause_video_for_overlay(self):
        """Temporarily pause video playback while an overlay is visible"""
        # Only save state if we're actually pausing (not if already paused)
        # This prevents overwriting video_was_playing when transitioning between overlays
        if self.is_playing:
            self.video_was_playing = self.is_playing
            
            # Use MediaListPlayer if available
            if config.video.use_medialist_player and self.list_player:
                try:
                    self.list_player.pause()
                except Exception:
                    pass
            elif self.player:
                try:
                    self.player.set_pause(True)
                except Exception:
                    try:
                        self.player.pause()
                    except Exception:
                        pass
            
            self.is_playing = False
        # If already paused, don't overwrite video_was_playing state

    def _resume_video_after_overlay(self, restart=False):
        """Resume playback after overlay without recreating decoders"""
        if self.video_was_playing:
            # Use MediaListPlayer if available
            if config.video.use_medialist_player and self.list_player:
                if restart:
                    # Restart current video in playlist
                    self.list_player.play_item_at_index(self.current_video_index)
                else:
                    # Resume playback
                    try:
                        self.list_player.play()
                    except Exception:
                        pass
            elif self.player:
                # Traditional player
                if restart:
                    self._rewind_current_media()
                try:
                    self.player.set_pause(False)
                except Exception:
                    self.player.play()
            
            self.is_playing = True
            
            # Schedule video status check with optimized interval
            check_interval = config.video.video_status_check_interval_ms if hasattr(config, 'video') else 2000
            self.root.after(check_interval, self.check_video_status)

        self.video_was_playing = False

    def _show_no_match_overlay(self, image_path):
        """Show no match image overlay for 5 seconds (thread-safe)"""
        try:
            if self.is_showing_image:
                return  # Already showing an image

            # Stop current video (we'll restart fresh after overlay)
            if self.is_playing:
                self._pause_video_for_overlay()
                print("⏸️ Video paused for no match image display")

            self.is_showing_image = True

            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            # Calculate image size (max 100% of screen)
            max_width = int(screen_width * 1.0)
            max_height = int(screen_height * 1.0)

            photo = self.image_manager.load_image_for_display(
                Path(image_path),
                (max_width, max_height),
                master=self.root
            )

            if photo:
                # Create FULL-SCREEN overlay frame
                self.image_overlay = tk.Toplevel(self.root)
                self.image_overlay.configure(background=config.display.product_image_background_color)
                self.image_overlay.attributes('-topmost', True)
                self.image_overlay.overrideredirect(True)
                self.image_overlay.configure(cursor="none")

                # Position to cover ENTIRE screen (including button area)
                self.image_overlay.geometry(f"{screen_width}x{screen_height}+0+0")

                # Create main container frame
                main_frame = tk.Frame(self.image_overlay, background=config.display.product_image_background_color)
                main_frame.pack(expand=True, fill='both')

                # Create content frame for centering
                content_frame = tk.Frame(main_frame, background=config.display.product_image_background_color)
                content_frame.place(relx=0.5, rely=0.5, anchor='center')

                # Add no match image (no text labels)
                image_label = tk.Label(
                    content_frame,
                    image=photo,
                    background=config.display.product_image_background_color
                )
                image_label.pack()

                # Keep reference to photo to prevent garbage collection
                self.image_overlay.photo = photo

                # Schedule hide after 5 seconds (as requested)
                self.image_display_timer = self.root.after(5000, self._hide_image_overlay)

                print(f"✅ Displaying no match image for 5 seconds")
            else:
                print("❌ Failed to load no match image for display")
                self._hide_image_overlay()

        except Exception as e:
            print(f"❌ Error showing no match image overlay: {e}")
            self._hide_image_overlay()

    def _show_qr_not_allowed_overlay(self, image_path):
        """Show QR not allowed image overlay for 5 seconds (thread-safe)"""
        try:
            if self.is_showing_image:
                return  # Already showing an image

            # Stop current video (we'll restart fresh after overlay)
            if self.is_playing:
                self._pause_video_for_overlay()
                print("⏸️ Video paused for QR code not allowed image display")

            self.is_showing_image = True

            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            # Calculate image size (max 100% of screen)
            max_width = int(screen_width * 1.0)
            max_height = int(screen_height * 1.0)

            photo = self.image_manager.load_image_for_display(
                Path(image_path),
                (max_width, max_height),
                master=self.root
            )

            if photo:
                # Create FULL-SCREEN overlay frame
                self.image_overlay = tk.Toplevel(self.root)
                self.image_overlay.configure(background=config.display.product_image_background_color)
                self.image_overlay.attributes('-topmost', True)
                self.image_overlay.overrideredirect(True)
                self.image_overlay.configure(cursor="none")

                # Position to cover ENTIRE screen (including button area)
                self.image_overlay.geometry(f"{screen_width}x{screen_height}+0+0")

                # Create main container frame
                main_frame = tk.Frame(self.image_overlay, background=config.display.product_image_background_color)
                main_frame.pack(expand=True, fill='both')

                # Create content frame for centering
                content_frame = tk.Frame(main_frame, background=config.display.product_image_background_color)
                content_frame.place(relx=0.5, rely=0.5, anchor='center')

                # Add QR not allowed image (no text labels)
                image_label = tk.Label(
                    content_frame,
                    image=photo,
                    background=config.display.product_image_background_color
                )
                image_label.pack()

                # Keep reference to photo to prevent garbage collection
                self.image_overlay.photo = photo

                # Schedule hide after 5 seconds (as requested)
                self.image_display_timer = self.root.after(5000, self._hide_image_overlay)

                print(f"✅ Displaying QR not allowed image for 5 seconds")
            else:
                print("❌ Failed to load QR not allowed image for display")
                self._hide_image_overlay()

        except Exception as e:
            print(f"❌ Error showing QR not allowed image overlay: {e}")
            self._hide_image_overlay()

    def _show_processing_overlay(self, image_path):
        """Show processing/verification image overlay (thread-safe)"""
        try:
            # Don't block if already showing processing overlay
            if self.processing_overlay and self.processing_overlay.winfo_exists():
                return  # Already showing processing image

            # Stop current video temporarily while showing processing image
            if self.is_playing:
                self._pause_video_for_overlay()
                print("⏸️ Video paused for processing image display")

            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            # Calculate image size (max 100% of screen)
            max_width = int(screen_width * 1.0)
            max_height = int(screen_height * 1.0)

            photo = self.image_manager.load_image_for_display(
                Path(image_path),
                (max_width, max_height),
                master=self.root
            )

            if photo:
                # Create FULL-SCREEN overlay frame
                self.processing_overlay = tk.Toplevel(self.root)
                self.processing_overlay.configure(background=config.display.product_image_background_color)
                self.processing_overlay.attributes('-topmost', True)
                self.processing_overlay.overrideredirect(True)
                self.processing_overlay.configure(cursor="none")

                # Position to cover ENTIRE screen (including button area)
                self.processing_overlay.geometry(f"{screen_width}x{screen_height}+0+0")

                # Create main container frame
                main_frame = tk.Frame(self.processing_overlay, background=config.display.product_image_background_color)
                main_frame.pack(expand=True, fill='both')

                # Create content frame for centering
                content_frame = tk.Frame(main_frame, background=config.display.product_image_background_color)
                content_frame.place(relx=0.5, rely=0.5, anchor='center')

                # Add processing image (no text labels)
                image_label = tk.Label(
                    content_frame,
                    image=photo,
                    background=config.display.product_image_background_color
                )
                image_label.pack()

                # Keep reference to photo to prevent garbage collection
                self.processing_overlay.photo = photo

                # Schedule auto-hide after 15 seconds as a safety timeout
                # (in case AWS response never arrives)
                # Use lambda to pass resume_video=True for timeout case
                self.processing_display_timer = self.root.after(
                    15000,
                    lambda: self._hide_processing_overlay(resume_video=True)
                )

                print(f"✅ Displaying processing/verification image (will auto-hide after 15s)")
            else:
                print("❌ Failed to load processing image for display")
                self._hide_processing_overlay(resume_video=True)

        except Exception as e:
            print(f"❌ Error showing processing image overlay: {e}")
            self._hide_processing_overlay(resume_video=True)
    
    def _show_image_overlay(self, image_path, product_name, product_brand="", barcode="", nfc_url=""):
        """Show full-screen image overlay with optional NFC QR code (thread-safe)"""
        try:
            if self.is_showing_image:
                return  # Already showing an image

            # Stop current video (we'll restart fresh after overlay)
            if self.is_playing:
                self._pause_video_for_overlay()
                print("⏸️ Video paused for image display")

            self.is_showing_image = True

            # Get screen dimensions
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            # Calculate image size (max 50% of screen for better text visibility)
            max_width = int(screen_width * 0.5)
            max_height = int(screen_height * 0.5)

            photo = self.image_manager.load_image_for_display(
                image_path,
                (max_width, max_height),
                master=self.root
            )

            if photo:
                # Create FULL-SCREEN overlay frame
                self.image_overlay = tk.Toplevel(self.root)
                self.image_overlay.configure(background=config.display.product_image_background_color)
                self.image_overlay.attributes('-topmost', True)
                self.image_overlay.overrideredirect(True)
                self.image_overlay.configure(cursor="none")

                # Position to cover ENTIRE screen (including button area)
                self.image_overlay.geometry(f"{screen_width}x{screen_height}+0+0")

                # Create main container frame
                main_frame = tk.Frame(self.image_overlay, background=config.display.product_image_background_color)
                main_frame.pack(expand=True, fill='both')

                # Create horizontal layout frame for product info and QR code
                horizontal_frame = tk.Frame(main_frame, background=config.display.product_image_background_color)
                horizontal_frame.place(relx=0.5, rely=0.5, anchor='center')

                # Left side: Product info
                content_frame = tk.Frame(horizontal_frame, background=config.display.product_image_background_color)
                content_frame.pack(side='left', padx=20)

                # Add product image
                image_label = tk.Label(
                    content_frame,
                    image=photo,
                    background=config.display.product_image_background_color
                )
                # CRITICAL: Keep reference on label to prevent garbage collection
                image_label.image = photo
                image_label.pack(pady=10)

                # Add product name
                name_label = tk.Label(
                    content_frame,
                    text=product_name,
                    fg='black',
                    background=config.display.product_image_background_color,
                    font=('Arial', 18, 'bold'),
                    wraplength=int(screen_width * 0.4),
                    justify='center'
                )
                name_label.pack(pady=10)

                # Keep reference to photo to prevent garbage collection
                self.image_overlay.photo = photo

                # Right side: QR code for NFC URL (if available)
                if nfc_url and QR_GENERATOR_AVAILABLE:
                    try:
                        # Generate large black QR code image
                        qr_size = 350  # Extra large for easy scanning
                        qr_image = generate_qr_code(nfc_url, size=qr_size)

                        if qr_image:
                            # Convert PIL image to PhotoImage
                            qr_photo = ImageTk.PhotoImage(qr_image, master=self.root)

                            # Create QR code frame on the right
                            qr_frame = tk.Frame(horizontal_frame, background=config.display.product_image_background_color)
                            qr_frame.pack(side='right', padx=30)

                            # Add QR code image
                            qr_label = tk.Label(
                                qr_frame,
                                image=qr_photo,
                                background=config.display.product_image_background_color
                            )
                            qr_label.image = qr_photo  # Keep reference
                            qr_label.pack(pady=10)

                            # Add "Scan For Rewards" text with large black font
                            scan_label = tk.Label(
                                qr_frame,
                                text="Scan For Rewards",
                                fg='black',
                                background=config.display.product_image_background_color,
                                font=('Arial', 24, 'bold'),
                                justify='center'
                            )
                            scan_label.pack(pady=10)

                            # Keep reference to QR photo
                            self.image_overlay.qr_photo = qr_photo
                            print("✅ QR code generated for NFC URL")
                    except Exception as qr_error:
                        print(f"⚠ Failed to generate QR code: {qr_error}")

                # Add countdown timer at bottom center
                self.countdown_seconds = 10
                self.countdown_label = tk.Label(
                    main_frame,
                    text=str(self.countdown_seconds),
                    fg='black',
                    background=config.display.product_image_background_color,
                    font=('Arial', 32, 'bold'),
                    justify='center'
                )
                self.countdown_label.place(relx=0.95, rely=0.95, anchor='se')

                # Start countdown updates
                self.countdown_timer_ids = []
                self._update_countdown()

                # Schedule hide after 10 seconds
                self.image_display_timer = self.root.after(10000, self._hide_image_overlay)

                print(f"✅ Displaying full-screen image: {product_name}")
            else:
                print("❌ Failed to load image for display")
                self._hide_image_overlay()
                
        except Exception as e:
            print(f"❌ Error showing image overlay: {e}")
            self._hide_image_overlay()
    
    def _hide_image_overlay(self):
        """Hide image overlay and restart video playback"""
        try:
            # Cancel countdown timers if active
            if hasattr(self, 'countdown_timer_ids'):
                for timer_id in self.countdown_timer_ids:
                    try:
                        self.root.after_cancel(timer_id)
                    except:
                        pass
                self.countdown_timer_ids = []

            # Explicitly clean up PhotoImage reference to prevent memory leak (Issue #85)
            if self.image_overlay and hasattr(self.image_overlay, 'photo'):
                self.image_overlay.photo = None

            if self.image_overlay:
                self.image_overlay.destroy()
                self.image_overlay = None

            # Cancel timer if still active
            if self.image_display_timer:
                self.root.after_cancel(self.image_display_timer)
                self.image_display_timer = None

            # Restart current video from beginning for clean experience
            if self.video_files:
                print("🔄 Restarting video after image display")
            self._resume_video_after_overlay(restart=True)

            self.is_showing_image = False
            print("✅ Image overlay hidden")

        except Exception as e:
            print(f"❌ Error hiding image overlay: {e}")
            self.is_showing_image = False
            # CRITICAL: Ensure video restarts even on error to prevent black screen
            try:
                if self.video_files:
                    self.is_playing = True
                    self.play_current_video(restart=True)
            except Exception as restart_error:
                print(f"❌ Failed to restart video after overlay error: {restart_error}")

    def _update_countdown(self):
        """Update the countdown timer display"""
        try:
            if hasattr(self, 'countdown_label') and self.countdown_label.winfo_exists():
                self.countdown_label.config(text=str(self.countdown_seconds))
                if self.countdown_seconds > 0:
                    self.countdown_seconds -= 1
                    timer_id = self.root.after(1000, self._update_countdown)
                    self.countdown_timer_ids.append(timer_id)
        except:
            pass  # Overlay may have been destroyed

    def _hide_processing_overlay(self, resume_video=False):
        """
        Hide processing/verification image overlay

        Args:
            resume_video: If True, resume video playback. If False (default), leave video
                         paused for next overlay to handle. Use True for timeout case.
        """
        try:
            # Explicitly clean up PhotoImage reference to prevent memory leak
            if self.processing_overlay and hasattr(self.processing_overlay, 'photo'):
                self.processing_overlay.photo = None

            if self.processing_overlay and self.processing_overlay.winfo_exists():
                self.processing_overlay.destroy()
                self.processing_overlay = None

            # Cancel timer if still active
            if self.processing_display_timer:
                self.root.after_cancel(self.processing_display_timer)
                self.processing_display_timer = None

            # Only resume video if explicitly requested (e.g., timeout case)
            # When transitioning to product/no-match overlay, leave video paused
            if resume_video and self.video_was_playing:
                self._resume_video_after_overlay(restart=False)

            print("✅ Processing overlay hidden")

        except Exception as e:
            print(f"❌ Error hiding processing overlay: {e}")
            # If resume was requested but failed, try to restart video
            if resume_video:
                try:
                    if self.video_files:
                        self.is_playing = True
                        self.play_current_video(restart=True)
                except Exception as restart_error:
                    print(f"❌ Failed to restart video after processing overlay error: {restart_error}")

    def display_deposit_waiting(self):
        """
        Display "Please Deposit Your Item" screen while waiting for IR sensor detection.

        Shown after AWS confirms barcode is valid (openDoor) and before door fully opens.
        Replaced by product image (success) or failure message (no detection).
        """
        if not self.root:
            return

        print("Displaying deposit waiting overlay...")
        self.root.after(0, self._show_deposit_waiting_overlay)

    def _show_deposit_waiting_overlay(self):
        """Show the deposit waiting overlay on the main thread"""
        try:
            # Hide processing image if still showing
            self._hide_processing_overlay()

            # Pause video if playing
            if self.is_playing:
                self._pause_video_for_overlay()

            # Destroy any existing overlays
            if self.image_overlay:
                self.image_overlay.destroy()
                self.image_overlay = None

            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            self.image_overlay = tk.Toplevel(self.root)
            self.image_overlay.attributes('-topmost', True)
            self.image_overlay.overrideredirect(True)
            self.image_overlay.geometry(f"{screen_width}x{screen_height}+0+0")
            self.image_overlay.configure(bg='#1a1a2e', cursor="none")

            container = tk.Frame(self.image_overlay, bg='#1a1a2e')
            container.pack(expand=True, fill='both')

            # Arrow-down icon
            icon_label = tk.Label(
                container,
                text="\u2193",
                font=('Arial', 100, 'bold'),
                fg='#3498db',
                bg='#1a1a2e'
            )
            icon_label.pack(pady=(80, 20))

            title_label = tk.Label(
                container,
                text="Please Deposit Your Item",
                font=('Arial', 36, 'bold'),
                fg='#ffffff',
                bg='#1a1a2e'
            )
            title_label.pack(pady=(0, 30))

            instruction_label = tk.Label(
                container,
                text="Place the item in the bin now",
                font=('Arial', 22),
                fg='#b0b0b0',
                bg='#1a1a2e',
                justify='center'
            )
            instruction_label.pack(pady=(0, 40))

            self.image_overlay.update()
            self.is_showing_image = True

            print("Deposit waiting overlay displayed")

        except Exception as e:
            logging.error(f"Failed to display deposit waiting overlay: {e}")
            print(f"Error displaying deposit waiting overlay: {e}")

    def hide_deposit_waiting(self):
        """
        Hide the deposit waiting overlay.

        Does NOT resume video — the next overlay (product image or failure)
        will handle video state.
        """
        if not self.root:
            return

        self.root.after(0, self._hide_deposit_waiting_overlay)

    def _hide_deposit_waiting_overlay(self):
        """Hide deposit waiting overlay on the main thread"""
        try:
            if self.image_overlay:
                self.image_overlay.destroy()
                self.image_overlay = None

            self.is_showing_image = False
            print("Deposit waiting overlay hidden")

        except Exception as e:
            print(f"Error hiding deposit waiting overlay: {e}")
            self.is_showing_image = False

    def display_recycle_failure(self, duration: float = 5.0):
        """
        Display "Item not detected" failure message on screen.

        Called when barcode was scanned but sensor did not detect item
        deposited before door closed.

        Args:
            duration: How long to show message (seconds)
        """
        if not self.root:
            return

        print("Displaying recycle failure overlay...")
        self.root.after(0, self._show_recycle_failure_overlay, duration)

    def _show_recycle_failure_overlay(self, duration: float):
        """Show recycle failure overlay on the main thread"""
        try:
            # Pause video if playing
            if self.is_playing:
                self._pause_video_for_overlay()

            # Destroy any existing overlays
            if self.image_overlay:
                self.image_overlay.destroy()
                self.image_overlay = None

            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()

            self.image_overlay = tk.Toplevel(self.root)
            self.image_overlay.attributes('-topmost', True)
            self.image_overlay.overrideredirect(True)
            self.image_overlay.geometry(f"{screen_width}x{screen_height}+0+0")
            self.image_overlay.configure(bg='#1a1a2e', cursor="none")

            container = tk.Frame(self.image_overlay, bg='#1a1a2e')
            container.pack(expand=True, fill='both')

            # Red X icon
            icon_label = tk.Label(
                container,
                text="X",
                font=('Arial', 120, 'bold'),
                fg='#e74c3c',
                bg='#1a1a2e'
            )
            icon_label.pack(pady=(80, 20))

            title_label = tk.Label(
                container,
                text="Item Not Detected",
                font=('Arial', 36, 'bold'),
                fg='#ffffff',
                bg='#1a1a2e'
            )
            title_label.pack(pady=(0, 20))

            instruction_label = tk.Label(
                container,
                text="Please scan again",
                font=('Arial', 24),
                fg='#b0b0b0',
                bg='#1a1a2e',
                justify='center'
            )
            instruction_label.pack(pady=(0, 40))

            self.image_overlay.update()
            self.is_showing_image = True

            # Auto-hide and resume video after duration
            self.image_display_timer = self.root.after(
                int(duration * 1000),
                self._hide_image_overlay
            )

            print("Recycle failure overlay displayed")

        except Exception as e:
            logging.error(f"Failed to display recycle failure: {e}")
            print(f"Error displaying failure overlay: {e}")
            try:
                if self.video_files and self.video_was_playing:
                    self.is_playing = True
                    self.play_current_video(restart=True)
            except Exception:
                pass

    def _cleanup_current_media(self):
        """Clean up current media resources, keep player for efficiency"""
        try:
            if self.player and self.current_media_path:
                # Just stop and clear current media, keep player
                self.player.stop()
                # Clear current media reference but don't release player
                self.current_media_path = None
        except Exception as e:
            print(f"⚠ Media cleanup error: {e}")
    
    def cleanup_and_exit(self):
        """Clean shutdown with comprehensive VLC cleanup"""
        print("🔄 Shutting down...")
        
        # Stop MediaListPlayer if in use
        if config.video.use_medialist_player and hasattr(self, 'list_player') and self.list_player:
            try:
                self.list_player.stop()
                self.list_player.release()
                self.list_player = None
                print("✓ MediaListPlayer stopped and released")
            except Exception as e:
                print(f"⚠ Error releasing MediaListPlayer: {e}")
        
        # Stop video and cleanup player
        if self.player:
            self.player.stop()
            self.player.release()
            self.player = None

        # Clean up all cached media with logging (traditional mode only)
        if hasattr(self, 'media_cache') and self.media_cache:
            for path, media in self.media_cache.items():
                try:
                    media.release()
                except Exception:
                    pass
            self.media_cache.clear()
            print("🧹 Released all cached VLC media objects")
        
        # Stop animations
        if hasattr(self, "bounce_animation_running"):
            self.stop_bounce_animation()

        # Stop barcode scanning
        self.barcode_scanner.stop_scanning()
        
        # Stop AWS publishing
        self.status_publish_active = False
        
        # Disconnect AWS manager only if we own it
        if self.aws_manager and self._owns_aws_manager:
            self.aws_manager.disconnect()
            # Servo cleanup is handled by AWS manager disconnect
        
        # Close window
        if self.root:
            self.root.destroy()

        # Release VLC instance
        if self.instance:
            try:
                self.instance.release()
            except Exception:
                pass
        
        print("✅ Shutdown complete")
        sys.exit(0)
    
    def on_barcode_scanned(self, barcode_data, transaction_id):
        """Handle barcode scans with minimal latency"""
        print(f'🎬 Barcode callback: {barcode_data} (ID: {transaction_id[:8]}...)')
        # Non-blocking video response
        try:
            self.root.after(0, self.next_video)  # Schedule on main thread
        except Exception as e:
            print(f"Callback error: {e}")
    
    def run(self):
        """Main application loop"""
        print("=" * 60)
        print("🎯 TSV6 Enhanced Video Player - OPTIMIZED")
        print("📡 AWS IoT Integration Active (Direct MQTT)")
        print("⚡ Near-instant barcode transmission enabled")
        print("=" * 60)
        
        # Setup video display
        self.setup_video_display()
        
        # Load videos
        self.load_videos()
        
        # Start AWS status publishing
        self.start_status_publishing()
        
        # Connect callback
        if self.barcode_scanner:
            self.barcode_scanner.barcode_callback = self.on_barcode_scanned
        
        # Start barcode scanning
        self.barcode_scanner.start_scanning()
        
        # Start video playback
        if self.video_files:
            self.play_current_video()
        else:
            print("⚠ No videos found - displaying standby screen")
            self.canvas.create_text(
                self.root.winfo_screenwidth()//2,
                self.root.winfo_screenheight()//2,
                text="TSV6 Ready\nOptimized Barcode Scanning Active\nNo Videos Found",
                fill="white",
                font=("Arial", 32),
                justify="center"
            )
        
        print("🚀 Application started - scan barcodes or use keys:")
        print("  • SPACE: Play/Pause")
        print("  • LEFT/RIGHT: Previous/Next video")
        print("  • ESC: Exit")
        print("⚡ Threading active: Scanner → Queue → Publisher")
        
        try:
            # Start GUI main loop
            self.root.mainloop()
        except KeyboardInterrupt:
            self.cleanup_and_exit()


def main():
    """Main entry point"""
    # Create and run the enhanced video player
    player = EnhancedVideoPlayer()
    player.run()


if __name__ == "__main__":
    main()
