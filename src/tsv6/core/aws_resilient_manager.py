#!/usr/bin/env python3
"""
Resilient AWS IoT Manager

Enhanced AWS IoT connection manager with robust error handling,
exponential backoff, circuit breaker pattern, and automatic recovery.
Designed for production IoT devices that need high reliability.
"""

import json
import time
import threading
import subprocess
import datetime
import random
import os
import socket
import logging
import uuid
import concurrent.futures
import traceback
import threading as _threading
import fcntl
from pathlib import Path
from typing import Optional, Callable, Dict, Any
from enum import Enum
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Import version utility for dynamic version management
try:
    from ..utils.version import get_firmware_version
except ImportError:
    # Fallback if version module not available
    def get_firmware_version():
        return "6.0.0"

# Import network diagnostics
try:
    from ..utils.network_diagnostics import NetworkDiagnostics
    NETWORK_DIAGNOSTICS_AVAILABLE = True
except ImportError:
    NETWORK_DIAGNOSTICS_AVAILABLE = False
    logger.warning("Network diagnostics not available")

# Import process management for unique client IDs and locking
try:
    from ..utils.process_manager import ClientIDGenerator, ProcessLock, DuplicateConnectionPrevention
    PROCESS_MANAGER_AVAILABLE = True
except ImportError:
    PROCESS_MANAGER_AVAILABLE = False
    print("⚠ Process manager not available - using basic client ID generation")

try:
    from awsiot import mqtt_connection_builder
    from awscrt import io, mqtt, auth, http
    from concurrent.futures import Future
    AWS_IOT_AVAILABLE = True
except ImportError:
    logger.warning("AWS IoT SDK not available. Install with: pip install awsiotsdk")
    AWS_IOT_AVAILABLE = False


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class RetryConfig:
    """Configuration for retry behavior"""
    initial_delay: float = 1.0  # OPTIMIZED: 2.0 → 1.0 for faster first retry (Issue #TS_538A7DD4)
    max_delay: float = 120.0  # OPTIMIZED: 300s → 120s cap for production IoT (was too conservative)
    multiplier: float = 1.5  # Slower backoff growth (was 2.0)
    jitter: float = 0.2  # Increased jitter to prevent thundering herd
    max_attempts: int = -1  # -1 for infinite retries


class CircuitBreaker:
    """Circuit breaker for AWS operations"""

    def __init__(self, failure_threshold: int = 10, timeout: float = 0):
        self.failure_threshold = failure_threshold
        self.timeout = timeout  # 0 = no timeout, infinite retries
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "closed"  # closed, open, half-open
    
    def can_execute(self) -> bool:
        if self.state == "closed":
            return True
        elif self.state == "open":
            # If timeout is 0, never block (infinite retries)
            if self.timeout == 0:
                self.state = "half-open"
                return True
            if time.time() - self.last_failure_time > self.timeout:
                self.state = "half-open"
                return True
            return False
        else:  # half-open
            return True
    
    def on_success(self):
        self.failure_count = 0
        self.state = "closed"
    
    def on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "open"


class ResilientAWSManager:
    """Resilient AWS IoT Manager with enhanced error handling and recovery"""
    
    # Class-level lock file for status publishing deduplication
    _status_publish_lock_file = "/tmp/tsv6-status-publish.lock"
    _status_publish_lock_handle = None
    _last_status_publish_time = 0.0
    _status_publish_lock = threading.Lock()
    
    def __init__(self, thing_name: str, endpoint: str, cert_path: str,
                 key_path: str, ca_path: str, retry_config: Optional[RetryConfig] = None,
                 use_unique_client_id: bool = True, lock_file: Optional[str] = None):
        self.thing_name = thing_name
        self.endpoint = endpoint
        self.cert_path = cert_path
        self.key_path = key_path
        self.ca_path = ca_path
        self.retry_config = retry_config or RetryConfig()
        
        # Process management for duplicate connection prevention (CRITICAL FIX for Issue #XX)
        self.use_unique_client_id = use_unique_client_id
        self.duplicate_prevention = None
        self.process_lock = None
        
        # Connection management
        self.connection = None
        self.state = ConnectionState.DISCONNECTED
        
        # Generate unique client ID to prevent DUPLICATE_CLIENTID errors
        if use_unique_client_id and PROCESS_MANAGER_AVAILABLE:
            self.duplicate_prevention = DuplicateConnectionPrevention(thing_name, lock_file)
            if self.duplicate_prevention.initialize():
                self.client_id = self.duplicate_prevention.get_client_id()
                self.session_id = self.duplicate_prevention.get_session_id()
            else:
                print("⚠️  Failed to initialize duplicate prevention - using basic client ID")
                self.client_id = f"{thing_name}-{os.getpid()}"
                self.session_id = None
        else:
            # Fallback: use thing_name as-is when unique ID generation is disabled
            self.client_id = thing_name
            self.session_id = None
        
        self.connection_start_time = None
        self._stop_reconnect = threading.Event()
        self._reconnect_thread = None
        
        # Circuit breakers for different operations
        self.publish_circuit_breaker = CircuitBreaker()
        self.connection_circuit_breaker = CircuitBreaker()
        
        # Topics
        self.status_topic = f"device/{thing_name}/status"
        self.shadow_update_topic = f"$aws/things/{thing_name}/shadow/update"
        self.command_topic = f"device/{thing_name}/command"
        self.barcode_response_topic = f"{thing_name}/openDoor"
        self.no_match_topic = f"{thing_name}/noMatch"
        self.lte_status_topic = f"device/{thing_name}/lte/status"  # Compact payload for 4G LTE
        
        # Callbacks
        self.on_connection_success: Optional[Callable] = None
        self.on_connection_lost: Optional[Callable] = None
        self.image_display_callback: Optional[Callable] = None
        self.no_match_display_callback: Optional[Callable] = None
        
        # Message queue for offline scenarios
        self._message_queue = []
        self._queue_lock = threading.Lock()
        
        # Cached LTE signal strength to avoid blocking startup (Issue: mmcli 10s timeout)
        # Initialize to -1 to indicate "not yet fetched" vs actual 0% signal
        self._cached_lte_signal: int = -1  # -1 = not yet fetched, 0-100 = valid signal
        self._lte_signal_lock = threading.Lock()
        self._lte_signal_thread: Optional[threading.Thread] = None
        
        logger.info(f"Resilient AWS Manager initialized for {thing_name}")
        
        # Acquire status publish lock file to prevent duplicate publishes across processes
        self._acquire_status_publish_lock()

    def _debug_publish_enabled(self) -> bool:
        """Enable verbose publish diagnostics when TSV6_DEBUG_AWS_PUBLISH=1.

        This is intentionally runtime-gated to avoid noisy logs in production.
        """
        return os.getenv("TSV6_DEBUG_AWS_PUBLISH", "0") == "1"

    def _debug_publish_log(self, event: str, *, topic: str = "", payload: Optional[Dict[str, Any]] = None, extra: Optional[Dict[str, Any]] = None) -> None:
        """Log publish diagnostics: pid/thread + call-site + message identifiers."""
        if not self._debug_publish_enabled():
            return

        try:
            thread = _threading.current_thread()
            ctx = {
                "event": event,
                "pid": os.getpid(),
                "thread": f"{thread.name}({thread.ident})",
                "thing": self.thing_name,
                "client_id": getattr(self, "client_id", None),
                "session_id": getattr(self, "session_id", None),
                "state": getattr(self, "state", None).value if getattr(self, "state", None) else None,
                "topic": topic,
            }

            # Extract useful identifiers from a shadow payload (if present)
            msg_id = None
            ts_iso = None
            conn_state = None
            try:
                reported = (payload or {}).get("state", {}).get("reported", {})
                msg_id = reported.get("messageId")
                ts_iso = reported.get("timestampISO")
                conn_state = reported.get("connectionState")
            except Exception:
                pass

            if msg_id is not None:
                ctx["messageId"] = msg_id
            if ts_iso is not None:
                ctx["timestampISO"] = ts_iso
            if conn_state is not None:
                ctx["connectionState"] = conn_state
            if extra:
                ctx.update(extra)

            # Best-effort caller identification (who invoked publish)
            try:
                stack = traceback.extract_stack(limit=12)
                # Pick a frame a few levels above this helper
                caller = stack[-4] if len(stack) >= 4 else stack[-1]
                ctx["caller"] = f"{caller.filename}:{caller.lineno} in {caller.name}"
            except Exception:
                pass

            logger.info("AWS_PUBLISH_DIAG %s", ctx)
        except Exception as e:
            logger.warning("AWS publish diag logging failed: %s", e)

    def set_callbacks(self, on_success: Optional[Callable] = None, 
                     on_lost: Optional[Callable] = None):
        """Set connection callbacks"""
        self.on_connection_success = on_success
        self.on_connection_lost = on_lost

    def set_image_display_callback(self, callback: Callable):
        """Set callback for image display"""
        self.image_display_callback = callback

    def set_no_match_display_callback(self, callback: Callable):
        """Set callback for no match display"""
        self.no_match_display_callback = callback

    @property
    def connected(self) -> bool:
        """Check if currently connected"""
        return self.state == ConnectionState.CONNECTED

    def connect(self) -> bool:
        """Connect to AWS IoT with retry logic"""
        if not AWS_IOT_AVAILABLE:
            logger.warning("AWS IoT SDK not available - using mock mode")
            self.state = ConnectionState.CONNECTED
            self.connection_start_time = time.time()
            return True
        
        if not self.connection_circuit_breaker.can_execute():
            logger.warning("Connection circuit breaker open")
            return False
        
        self.state = ConnectionState.CONNECTING
        
        try:
            # Verify certificate files
            cert_files = [self.cert_path, self.key_path, self.ca_path]
            missing_files = [f for f in cert_files if not Path(f).exists()]
            
            if missing_files:
                logger.error(f"Missing certificate files: {missing_files}")
                self.connection_circuit_breaker.on_failure()
                self.state = ConnectionState.FAILED
                return False

            logger.info(f"Connecting to AWS IoT: {self.endpoint}")
            
            # Create connection
            event_loop_group = io.EventLoopGroup(1)
            host_resolver = io.DefaultHostResolver(event_loop_group)
            client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

            self.connection = mqtt_connection_builder.mtls_from_path(
                endpoint=self.endpoint,
                cert_filepath=self.cert_path,
                pri_key_filepath=self.key_path,
                ca_filepath=self.ca_path,
                client_bootstrap=client_bootstrap,
                client_id=self.client_id,
                clean_session=False,
                keep_alive_secs=120,  # Increased from 60s to 120s for weak WiFi stability
                ping_timeout_secs=10,   # Increased from 5s to 10s for weak WiFi environments
                socket_timeout_secs=30,  # Increased socket timeout for stability
                connect_timeout_secs=20,  # Increased from default to ensure connection establishment
                will_message=None,
                will_qos=mqtt.QoS.AT_LEAST_ONCE,
                will_retain=False
            )

            # Set callbacks
            self.connection.on_connection_interrupted = self._on_connection_interrupted
            self.connection.on_connection_resumed = self._on_connection_resumed

            # Connect with timeout
            connect_future = self.connection.connect()
            connect_future.result(timeout=30)
            
            self.state = ConnectionState.CONNECTED
            self.connection_start_time = time.time()
            self.connection_circuit_breaker.on_success()

            logger.info(f"Connected to AWS IoT as {self.client_id}")
            self._debug_publish_log("connect_success", extra={"connected": True})

            # Subscribe to topics
            self._subscribe_to_topics()
            
            # Process queued messages
            self._process_message_queue()
            
            # Notify callback
            if self.on_connection_success:
                self.on_connection_success()
            
            return True
            
        except Exception as e:
            logger.error(f"AWS IoT connection failed: {e}")
            self._debug_publish_log("connect_failure", extra={"error": str(e), "error_type": type(e).__name__})
            self.connection_circuit_breaker.on_failure()
            self.state = ConnectionState.FAILED
            return False

    def _subscribe_to_topics(self):
        """Subscribe to necessary topics"""
        if not self.connection:
            return
        
        try:
            # Command topic
            subscribe_future, _ = self.connection.subscribe(
                topic=self.command_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_command_received
            )
            subscribe_future.result(timeout=10)
            
            # Barcode response topic
            subscribe_future, _ = self.connection.subscribe(
                topic=self.barcode_response_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_barcode_response_received
            )
            subscribe_future.result(timeout=10)

            # No-match topic
            subscribe_future, _ = self.connection.subscribe(
                topic=self.no_match_topic,
                qos=mqtt.QoS.AT_LEAST_ONCE,
                callback=self._on_no_match_received
            )
            subscribe_future.result(timeout=10)

            logger.info("Subscribed to all topics")

        except Exception as e:
            logger.warning(f"Failed to subscribe to some topics: {type(e).__name__}: {e or repr(e)}")

    def _on_connection_interrupted(self, connection, error, **kwargs):
        """Handle connection interruption with enhanced diagnostics"""
        connection_duration = time.time() - (self.connection_start_time or time.time())

        logger.warning("AWS CONNECTION INTERRUPTED")
        logger.warning(f"Error: {error}, Current state: {self.state.value}, Connection duration: {connection_duration:.1f}s, Error type: {type(error).__name__}")

        # Enhanced error detection
        error_str = str(error).upper()
        if "KEEP" in error_str and "ALIVE" in error_str:
            logger.warning("KEEP-ALIVE TIMEOUT - Device failed to send heartbeat! This indicates the connection is stuck or blocked")
            # Force immediate reconnection attempt for keep-alive timeouts
            self.state = ConnectionState.DISCONNECTED
        elif "UNEXPECTED_HANGUP" in error_str:
            logger.warning("MQTT hangup detected - network/keepalive issue")
            # Force immediate reconnection attempt for hangup errors
            self.state = ConnectionState.DISCONNECTED
        else:
            self.state = ConnectionState.RECONNECTING

        logger.warning(f"New state: {self.state.value}, Auto-reconnect will {'IMMEDIATELY' if self.state == ConnectionState.DISCONNECTED else 'attempt to'} restore connection")

        logger.error(f"AWS IoT connection interrupted: {error}")

        # Trigger callback with enhanced error information
        if self.on_connection_lost:
            enhanced_error = {
                "error": str(error),
                "error_type": type(error).__name__,
                "connection_duration": connection_duration,
                "timestamp": time.time()
            }
            self.on_connection_lost(enhanced_error)

    def _on_connection_resumed(self, connection, return_code, session_present, **kwargs):
        """Handle connection resumption"""
        logger.info(f"AWS IoT connection resumed: {return_code}")
        self.state = ConnectionState.CONNECTED
        if not self.connection_start_time:
            self.connection_start_time = time.time()

        # Re-subscribe to topics
        self._subscribe_to_topics()

        # Process any queued messages
        self._process_message_queue()

    def start_auto_reconnect(self):
        """Start automatic reconnection in background"""
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            return
        
        self._stop_reconnect.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, 
            name="AWS-Reconnect",
            daemon=True
        )
        self._reconnect_thread.start()
        logger.info("Auto-reconnect started")

    def stop_auto_reconnect(self):
        """Stop automatic reconnection"""
        self._stop_reconnect.set()
        if self._reconnect_thread:
            self._reconnect_thread.join(timeout=2)

    def _reconnect_loop(self):
        """Enhanced background reconnection loop with improved error handling"""
        attempt = 0
        delay = self.retry_config.initial_delay
        consecutive_failures = 0
        last_failure_time = 0
        
        while not self._stop_reconnect.is_set():
            if self.state in [ConnectionState.DISCONNECTED, ConnectionState.FAILED]:
                # Reset connection state before attempting reconnection
                if self.connection:
                    try:
                        # Force cleanup of existing connection
                        self.connection = None
                    except:
                        pass
                
                attempt += 1
                consecutive_failures += 1
                
                if self.retry_config.max_attempts > 0 and attempt > self.retry_config.max_attempts:
                    logger.error(f"Max reconnection attempts ({self.retry_config.max_attempts}) reached")
                    break

                # Enhanced diagnostics for reconnection attempts
                time_since_last_failure = time.time() - last_failure_time if last_failure_time > 0 else 0
                logger.info(f"AWS reconnection attempt #{attempt}, Current state: {self.state.value}, Delay: {delay:.1f}s, Consecutive failures: {consecutive_failures}, Time since last failure: {time_since_last_failure:.1f}s")
                
                # Add connection validation before attempting
                if self._validate_connection_prerequisites():
                    if self.connect():
                        logger.info("Reconnection successful")
                        attempt = 0
                        consecutive_failures = 0
                        delay = self.retry_config.initial_delay
                    else:
                        last_failure_time = time.time()
                        # Enhanced backoff for consecutive failures
                        if consecutive_failures > 3:
                            # Run network diagnostics after multiple failures
                            if NETWORK_DIAGNOSTICS_AVAILABLE and consecutive_failures == 5:
                                logger.info("Running network diagnostics after multiple failures...")
                                try:
                                    diagnostics = NetworkDiagnostics(self.endpoint)
                                    diag_results = diagnostics.run_full_diagnostics()
                                    diagnostics.print_diagnostics(diag_results)

                                    # Adjust delay based on diagnostics
                                    if diag_results["summary"]["overall_status"] == "failed":
                                        delay = min(delay * 2.0, self.retry_config.max_delay)
                                        logger.warning("Network issues detected - increasing reconnection delay")
                                except Exception as e:
                                    logger.warning(f"Network diagnostics failed: {e}")

                            delay = min(delay * 1.5, self.retry_config.max_delay)
                        else:
                            delay = min(delay * self.retry_config.multiplier, self.retry_config.max_delay)
                else:
                    logger.error("Connection prerequisites failed - skipping attempt")
                    last_failure_time = time.time()

                # Exponential backoff with jitter
                jitter = random.uniform(0, delay * self.retry_config.jitter)
                sleep_time = delay + jitter

                logger.debug(f"Waiting {sleep_time:.1f}s before next attempt...")
                self._stop_reconnect.wait(sleep_time)
            else:
                # Connected, reset counters and wait
                if consecutive_failures > 0:
                    logger.info(f"Connection stable - resetting failure counters (was {consecutive_failures})")
                    consecutive_failures = 0
                attempt = 0
                delay = self.retry_config.initial_delay
                self._stop_reconnect.wait(30)  # Check every 30 seconds
    
    def _validate_connection_prerequisites(self) -> bool:
        """Validate that all prerequisites for connection are met"""
        try:
            # Check certificate files exist and are readable
            cert_files = [self.cert_path, self.key_path, self.ca_path]
            for cert_file in cert_files:
                if not Path(cert_file).exists():
                    logger.error(f"Certificate file missing: {cert_file}")
                    return False
                if not os.access(cert_file, os.R_OK):
                    logger.error(f"Certificate file not readable: {cert_file}")
                    return False

            # Check network connectivity to the configured endpoint directly
            import socket
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(5)
                result = sock.connect_ex((self.endpoint, 8883))
                sock.close()
                if result != 0:
                    logger.error(f"Network connectivity test failed to {self.endpoint}:8883")
                    return False
            except Exception as e:
                logger.error(f"Network connectivity test error: {e}")
                return False

            return True

        except Exception as e:
            logger.error(f"Connection prerequisite validation failed: {e}")
            return False

    def publish_with_retry(self, topic: str, payload: Dict[str, Any],
                          retries: int = 3, use_qos0: bool = False) -> bool:
        """Publish with retry logic

        IMPORTANT: Only retries on connection errors BEFORE the message is sent.
        Once publish() is called, the message is considered sent and we don't retry
        to avoid duplicate messages on the broker side.

        Args:
            topic: MQTT topic to publish to
            payload: Message payload as dictionary
            retries: Number of connection retries (default 3)
            use_qos0: If True, use QoS 0 (fire-and-forget) to prevent MQTT-level
                      duplicates on slow connections. Recommended for periodic
                      status updates where occasional message loss is acceptable.
        """
        if not self.publish_circuit_breaker.can_execute():
            logger.warning("Publish circuit breaker open, queueing message")
            self._debug_publish_log("publish_blocked_circuit_open", topic=topic, payload=payload)
            self._queue_message(topic, payload)
            return False

        if not self.connected:
            logger.warning("Not connected, queueing message")
            self._debug_publish_log("publish_not_connected_queue", topic=topic, payload=payload)
            self._queue_message(topic, payload)
            return False

        # Convert payload to JSON with safe serialization (do once, outside retry loop)
        try:
            json_payload = json.dumps(payload, default=str)
        except (TypeError, ValueError) as json_err:
            logger.warning(f"JSON serialization warning: {json_err}")
            # Fallback: convert all non-serializable objects to strings
            safe_payload = {k: str(v) if not isinstance(v, (str, int, float, bool, list, dict, type(None))) else v
                            for k, v in payload.items()}
            json_payload = json.dumps(safe_payload)

        for attempt in range(retries + 1):
            try:
                # Check connection state before attempting publish
                if not self.connected or not self.connection:
                    raise ConnectionError("Not connected to AWS IoT")

                # Use QoS 0 for status updates to prevent MQTT-level duplicates on LTE
                # QoS 1 can cause duplicates when ACK is delayed on slow connections
                qos_level = mqtt.QoS.AT_MOST_ONCE if use_qos0 else mqtt.QoS.AT_LEAST_ONCE

                self._debug_publish_log(
                    "publish_attempt",
                    topic=topic,
                    payload=payload,
                    extra={"attempt": attempt + 1, "retries": retries, "qos": str(qos_level)}
                )

                publish_future, _ = self.connection.publish(
                    topic=topic,
                    payload=json_payload,
                    qos=qos_level
                )

                # Message was sent to broker. Wait for ACK.
                # Only treat TIMEOUT as success (message likely delivered).
                # Other errors indicate real failures that should be retried.
                try:
                    publish_future.result(timeout=10)
                except concurrent.futures.TimeoutError as timeout_err:
                    # ACK timed out but message was sent - consider success to avoid duplicates
                    logger.warning(f"Publish ACK timeout (message likely delivered): {timeout_err}")
                    self._debug_publish_log("publish_ack_timeout_considered_success", topic=topic, payload=payload)
                    self.publish_circuit_breaker.on_success()
                    return True
                except Exception as ack_error:
                    # Real error after publish - log and let outer handler decide
                    error_str = str(ack_error).lower()
                    if 'timeout' in error_str or 'timed out' in error_str:
                        # Timeout-like error, message likely sent
                        logger.warning(f"Publish ACK timeout (message likely delivered): {ack_error}")
                        self._debug_publish_log("publish_ack_timeout_like_considered_success", topic=topic, payload=payload)
                        self.publish_circuit_breaker.on_success()
                        return True
                    # Non-timeout error - this is a real failure, re-raise to retry
                    raise

                self.publish_circuit_breaker.on_success()
                self._debug_publish_log("publish_success", topic=topic, payload=payload)
                return True

            except ConnectionError as e:
                # Connection error - message was NOT sent, safe to retry
                logger.error(f"Publish attempt {attempt + 1} connection error: {e}")
                self._debug_publish_log("publish_connection_error", topic=topic, payload=payload, extra={"attempt": attempt + 1, "error": str(e)})
                if attempt < retries:
                    time.sleep(2 ** attempt)  # Exponential backoff
                else:
                    self.publish_circuit_breaker.on_failure()
                    self._queue_message(topic, payload)
            except Exception as e:
                # Other error during publish setup - safe to retry since message wasn't sent yet
                error_str = str(e).lower()
                if 'timeout' in error_str or 'timed out' in error_str:
                    # If we got a timeout and we're not sure if message was sent,
                    # log it but don't retry to avoid duplicates
                    logger.warning(f"Publish timeout (avoiding retry to prevent duplicates): {e}")
                    self._debug_publish_log("publish_timeout_avoiding_retry", topic=topic, payload=payload, extra={"attempt": attempt + 1, "error": str(e)})
                    return False

                logger.error(f"Publish attempt {attempt + 1} failed: {type(e).__name__}: {e or repr(e)}")
                self._debug_publish_log("publish_failed", topic=topic, payload=payload, extra={"attempt": attempt + 1, "error": str(e), "error_type": type(e).__name__})
                if attempt < retries:
                    time.sleep(2 ** attempt)
                else:
                    self.publish_circuit_breaker.on_failure()
                    self._queue_message(topic, payload)

        return False

    def _queue_message(self, topic: str, payload: Dict[str, Any]):
        """Queue message for later delivery"""
        with self._queue_lock:
            if len(self._message_queue) >= 100:  # Limit queue size
                self._message_queue.pop(0)  # Remove oldest
            
            self._message_queue.append({
                'topic': topic,
                'payload': payload,
                'timestamp': time.time()
            })
            logger.info(f"Message queued ({len(self._message_queue)} total)")
            self._debug_publish_log("queued", topic=topic, payload=payload, extra={"queue_len": len(self._message_queue)})

    def _process_message_queue(self):
        """Process queued messages"""
        with self._queue_lock:
            if not self._message_queue:
                return

            logger.info(f"Processing {len(self._message_queue)} queued messages...")
            self._debug_publish_log("queue_process_begin", extra={"queue_len": len(self._message_queue)})
            processed = 0
            
            # Process messages in batches to avoid blocking
            for msg in list(self._message_queue):
                if not self.connected:
                    break
                
                # Skip very old messages (older than 1 hour)
                if time.time() - msg['timestamp'] > 3600:
                    self._message_queue.remove(msg)
                    continue
                
                try:
                    self._debug_publish_log("queue_publish_attempt", topic=msg['topic'], payload=msg['payload'], extra={"queue_len": len(self._message_queue)})
                    publish_future, _ = self.connection.publish(
                        topic=msg['topic'],
                        payload=json.dumps(msg['payload']),
                        qos=mqtt.QoS.AT_LEAST_ONCE
                    )
                    publish_future.result(timeout=5)
                    self._message_queue.remove(msg)
                    processed += 1
                    self._debug_publish_log("queue_publish_success_removed", topic=msg['topic'], payload=msg['payload'], extra={"processed": processed, "queue_len": len(self._message_queue)})
                except Exception as e:
                    logger.error(f"Failed to send queued message: {e}")
                    self._debug_publish_log("queue_publish_failed_kept", topic=msg['topic'], payload=msg['payload'], extra={"error": str(e), "error_type": type(e).__name__})
                    break

            if processed > 0:
                logger.info(f"Processed {processed} queued messages")

    def _acquire_status_publish_lock(self):
        """Acquire inter-process lock for status publishing to prevent duplicates"""
        try:
            # Create lock file directory if needed
            Path(self._status_publish_lock_file).parent.mkdir(parents=True, exist_ok=True)
            
            # Try to open and lock the file (non-blocking)
            lock_handle = open(self._status_publish_lock_file, 'w')
            try:
                fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Write our PID for debugging
                lock_handle.write(f"pid={os.getpid()}\n")
                lock_handle.write(f"thing={self.thing_name}\n")
                lock_handle.write(f"client_id={self.client_id}\n")
                lock_handle.flush()
                self._status_publish_lock_handle = lock_handle
                logger.info(f"Status publish lock acquired: {self._status_publish_lock_file}")
            except IOError:
                # Lock is held by another process
                lock_handle.close()
                logger.warning(f"Status publish lock already held by another process (PID {self._read_lock_pid()})")
                self._status_publish_lock_handle = None
        except Exception as e:
            logger.warning(f"Failed to acquire status publish lock: {e}")
            self._status_publish_lock_handle = None
    
    def _read_lock_pid(self) -> Optional[str]:
        """Read PID from lock file for debugging"""
        try:
            with open(self._status_publish_lock_file, 'r') as f:
                for line in f:
                    if line.startswith('pid='):
                        return line.strip().split('=', 1)[1]
        except Exception:
            pass
        return None
    
    def _release_status_publish_lock(self):
        """Release inter-process lock for status publishing"""
        if self._status_publish_lock_handle:
            try:
                fcntl.flock(self._status_publish_lock_handle, fcntl.LOCK_UN)
                self._status_publish_lock_handle.close()
                if Path(self._status_publish_lock_file).exists():
                    Path(self._status_publish_lock_file).unlink()
                logger.info(f"Status publish lock released: {self._status_publish_lock_file}")
            except Exception as e:
                logger.warning(f"Error releasing status publish lock: {e}")
            finally:
                self._status_publish_lock_handle = None
    
    def publish_status(self) -> bool:
        """Publish device status with deduplication to prevent duplicate publishes"""
        # Check if we hold the status publish lock
        if self._status_publish_lock_handle is None:
            logger.warning("Status publish lock not held - skipping publish to prevent duplicates")
            return False
        
        # Inter-process deduplication: check minimum interval between publishes
        min_publish_interval = 30.0  # Minimum 30 seconds between status publishes
        current_time = time.time()
        time_since_last = current_time - ResilientAWSManager._last_status_publish_time
        
        if time_since_last < min_publish_interval:
            logger.info(f"Status publish skipped (too soon: {time_since_last:.1f}s < {min_publish_interval}s)")
            return False
        
        # Update last publish time (with thread safety)
        with ResilientAWSManager._status_publish_lock:
            ResilientAWSManager._last_status_publish_time = current_time
        
        try:
            # Get system info
            wifi_ssid, wifi_strength = self._get_wifi_info()
            cpu_temp = self._get_cpu_temperature()

            # Check if LTE is primary - use compact payload to reduce data costs
            if self._is_lte_primary():
                # Parse signal strength (may be "XX%" string or int)
                signal_value = wifi_strength
                if isinstance(wifi_strength, str):
                    # Extract numeric value from "XX%" or handle "Connecting..."
                    if wifi_strength == "Connecting...":
                        signal_value = -1
                    else:
                        signal_value = int(wifi_strength.replace('%', ''))

                lte_payload = self._build_lte_status_payload(wifi_ssid, signal_value, cpu_temp)
                logger.info(f"Publishing LTE compact status to {self.lte_status_topic}")
                self._debug_publish_log("lte_status_publish", topic=self.lte_status_topic, payload=lte_payload)
                return self.publish_with_retry(self.lte_status_topic, lte_payload, use_qos0=True)

            # WiFi mode: use full shadow payload for backward compatibility
            # Generate unique message ID to track duplicates (full UUID to avoid collisions)
            message_id = str(uuid.uuid4())

            status = {
                "thingName": self.thing_name,
                "deviceType": "raspberry-pi",
                "firmwareVersion": get_firmware_version(),  # Dynamically read from pyproject.toml
                "wifiSSID": wifi_ssid,
                "wifiStrength": wifi_strength,
                "temperature": cpu_temp,
                "timestampISO": datetime.datetime.utcnow().isoformat() + "Z",
                "timeConnectedMins": int((time.time() - (self.connection_start_time or time.time())) / 60),
                "connectionState": self.state.value,
                "messageId": message_id  # Unique ID to track duplicates
            }

            shadow_payload = {
                "state": {
                    "reported": status
                }
            }

            logger.info(f"Publishing status with messageId: {message_id}")
            self._debug_publish_log("status_publish_call", topic=self.shadow_update_topic, payload=shadow_payload)
            # Use QoS 0 for status updates - prevents MQTT-level duplicates on LTE
            # Status is periodic (every 5 min), so occasional loss is acceptable
            return self.publish_with_retry(self.shadow_update_topic, shadow_payload, use_qos0=True)

        except Exception as e:
            logger.error(f"Failed to publish status: {type(e).__name__}: {e or repr(e)}")
            return False

    def _is_lte_primary(self) -> bool:
        """Check if LTE is the primary connection by examining the default route."""
        try:
            env = os.environ.copy()
            env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')
            lte_interface = os.getenv('LTE_INTERFACE', 'wwan0')

            result = subprocess.run(
                ["ip", "route", "show", "default"],
                capture_output=True, text=True, timeout=5, env=env
            )
            if result.returncode == 0:
                lines = result.stdout.strip().split('\n')
                if lines:
                    # First default route is the primary (lowest metric)
                    first_route = lines[0]
                    if lte_interface in first_route:
                        return True
        except Exception:
            pass
        return False

    def _build_lte_status_payload(self, wifi_ssid: str, wifi_strength: int, cpu_temp: float) -> dict:
        """Build compact status payload for 4G LTE mode (~75% smaller than full payload).

        Key mapping:
            n = thingName
            s = wifiSSID (actually LTE SSID)
            w = wifiStrength (signal strength)
            t = temperature (CPU temp in F)
            m = timeConnectedMins
            c = connectionState
        """
        return {
            "n": self.thing_name,
            "s": wifi_ssid,
            "w": wifi_strength,
            "t": cpu_temp,
            "m": int((time.time() - (self.connection_start_time or time.time())) / 60),
            "c": self.state.value
        }

    def _get_wifi_info(self) -> tuple[str, int]:
        """Get connection information (WiFi or LTE based on primary route)"""
        try:
            env = os.environ.copy()
            env['PATH'] = '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:' + env.get('PATH', '')

            # Get configured interfaces from environment
            wifi_interface = os.getenv('WIFI_INTERFACE', 'wlan0')
            lte_interface = os.getenv('LTE_INTERFACE', 'wwan0')

            # Check if LTE is the primary connection by looking at default route
            lte_primary = False
            try:
                result = subprocess.run(
                    ["ip", "route", "show", "default"],
                    capture_output=True, text=True, timeout=5, env=env
                )
                if result.returncode == 0:
                    lines = result.stdout.strip().split('\n')
                    if lines:
                        # First default route is the primary (lowest metric)
                        first_route = lines[0]
                        if lte_interface in first_route:
                            lte_primary = True
            except Exception:
                pass

            # If LTE is primary, return LTE info
            if lte_primary:
                # Get LTE signal strength from ModemManager (as percentage with % symbol)
                pct = self._get_lte_signal_strength(env)
                # Handle -1 (not yet fetched) - show "Connecting..." instead of misleading "0%"
                if pct < 0:
                    return "LTE Hologram", "Connecting..."
                return "LTE Hologram", f"{pct}%"

            # Otherwise get WiFi info
            ssid = ""
            for cmd in (["/usr/sbin/iwgetid", "-r"], ["iwgetid", "-r"]):
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
                    if result.returncode == 0:
                        ssid = result.stdout.strip()
                        if ssid:
                            break
                except Exception:
                    continue

            if not ssid:
                ssid = "Unknown"

            rssi = None
            iwconfig_cmds = [
                ["/usr/sbin/iwconfig", wifi_interface],
                ["iwconfig", wifi_interface],
                ["/usr/sbin/iwconfig"],
                ["iwconfig"],
            ]

            for cmd in iwconfig_cmds:
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=5, env=env)
                except Exception:
                    continue

                if result.returncode != 0:
                    continue

                for line in result.stdout.splitlines():
                    if 'Signal level=' in line:
                        signal_part = line.split('Signal level=')[1].split()[0]
                        # Handle both formats: "-70 dBm" and "47/70" (quality ratio)
                        if '/' in signal_part:
                            # Quality ratio format (e.g., "47/70") - convert to approximate dBm
                            # Formula: dBm ≈ (quality / max_quality) * 100 - 110
                            # This maps 0/70 → -110 dBm and 70/70 → -10 dBm
                            try:
                                parts = signal_part.split('/')
                                quality = int(parts[0])
                                max_quality = int(parts[1]) if len(parts) > 1 else 70
                                # Convert quality ratio to approximate dBm
                                rssi = int((quality / max_quality) * 100 - 110)
                                break
                            except (ValueError, ZeroDivisionError):
                                continue
                        else:
                            # Standard dBm format
                            signal_part = signal_part.replace('dBm', '')
                            try:
                                rssi = int(signal_part)
                                break
                            except ValueError:
                                continue
                if rssi is not None:
                    break

            if rssi is None:
                rssi = -100

            return ssid, rssi
        except Exception:
            return "Unknown", -100

    def _get_lte_signal_strength(self, env: dict) -> int:
        """Get LTE signal strength as percentage from ModemManager (non-blocking).
        
        Returns cached value immediately and updates in background to avoid
        blocking startup/UI with mmcli's potential 10s+ response time.
        
        Returns:
            int: Signal strength percentage (0-100), or -1 if not yet fetched.
                 Callers should check for -1 and display "Connecting..." or similar.
        """
        # Start background refresh if not already running
        self._refresh_lte_signal_async(env)
        
        # Return cached value immediately (non-blocking)
        # NOTE: -1 means "not yet fetched" - caller should handle this
        with self._lte_signal_lock:
            return self._cached_lte_signal
    
    def _refresh_lte_signal_async(self, env: dict) -> None:
        """Refresh LTE signal strength in background thread."""
        # Don't start if already running
        if self._lte_signal_thread and self._lte_signal_thread.is_alive():
            return
        
        def fetch_signal():
            try:
                # Get signal quality percentage from modem status
                # Use shorter timeout (3s) to fail fast if modem is unresponsive
                result = subprocess.run(
                    ["mmcli", "-m", "0"],
                    capture_output=True, text=True, timeout=3, env=env
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        if 'signal quality' in line.lower():
                            # Format: "|          signal quality: XX% (cached)"
                            parts = line.split(':')
                            if len(parts) >= 2:
                                value = parts[1].strip().replace('%', '').split()[0]
                                try:
                                    with self._lte_signal_lock:
                                        self._cached_lte_signal = int(value)
                                    return
                                except ValueError:
                                    pass
            except subprocess.TimeoutExpired:
                logger.debug("mmcli timeout - modem may be initializing")
            except Exception as e:
                logger.debug(f"LTE signal fetch error: {e}")
        
        self._lte_signal_thread = threading.Thread(
            target=fetch_signal,
            name="LTE-Signal-Fetch",
            daemon=True
        )
        self._lte_signal_thread.start()

    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature in Fahrenheit"""
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_celsius = int(f.read().strip()) / 1000.0
                temp_fahrenheit = (temp_celsius * 9/5) + 32
                return round(temp_fahrenheit, 1)
        except:
            return 75.0

    def _on_command_received(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle received commands"""
        try:
            message = json.loads(payload.decode('utf-8'))
            logger.info(f"Command received: {message}")
        except Exception as e:
            logger.error(f"Error processing command: {e}")

    def _on_barcode_response_received(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle barcode responses"""
        try:
            message = json.loads(payload.decode('utf-8'))
            logger.info(f"Barcode response: {message}")

            if (message.get('thingName') == self.thing_name and
                message.get('returnAction') == 'openDoor'):

                logger.info(f"Opening door for: {message.get('productName', 'Unknown')}")

                # Trigger image display if callback is set
                if self.image_display_callback:
                    self.image_display_callback(message)

        except Exception as e:
            logger.error(f"Error processing barcode response: {e}")


    def _on_no_match_received(self, topic, payload, dup, qos, retain, **kwargs):
        """Handle no-match responses"""
        try:
            message = json.loads(payload.decode('utf-8'))
            logger.info(f"NoMatch response: {message}")
            if self.no_match_display_callback:
                self.no_match_display_callback()
        except Exception as e:
            logger.error(f"Error processing noMatch: {e}")

    def disconnect(self):
        """Clean disconnection with process lock cleanup"""
        logger.info("Disconnecting from AWS IoT...")
        # Stop reconnection
        self.stop_auto_reconnect()

        if self.connection and self.state == ConnectionState.CONNECTED:
            try:
                disconnect_future = self.connection.disconnect()
                disconnect_future.result(timeout=10)
                logger.info("Cleanly disconnected from AWS IoT")
            except Exception as e:
                logger.warning(f"Error during disconnect: {e}")
            finally:
                self.state = ConnectionState.DISCONNECTED
                self.connection = None
                self.connection_start_time = None
        
        # Clean up process lock
        if self.duplicate_prevention:
            self.duplicate_prevention.cleanup()
            logger.info("Process lock released")
        
        # Clean up status publish lock
        self._release_status_publish_lock()
        
        # Clean up status publish lock
        self._release_status_publish_lock()

    def get_status(self) -> Dict[str, Any]:
        """Get current manager status"""
        return {
            'state': self.state.value,
            'connected': self.connected,
            'connection_time': self.connection_start_time,
            'queued_messages': len(self._message_queue),
            'publish_circuit_state': self.publish_circuit_breaker.state,
            'connection_circuit_state': self.connection_circuit_breaker.state
        }

    # OTA Integration Methods
    def set_ota_manager(self, ota_manager):
        """Set OTA manager for handling updates"""
        self.ota_manager = ota_manager
        logger.info("OTA Manager registered with AWS Manager")

    def initialize_ota_capabilities(self):
        """Initialize OTA capabilities if OTA manager is available"""
        if hasattr(self, 'ota_manager') and self.ota_manager:
            if self.connected:
                success = self.ota_manager.initialize_jobs_client()
                if success:
                    logger.info("OTA capabilities initialized")
                    return True
                else:
                    logger.error("Failed to initialize OTA capabilities")
                    return False
            else:
                logger.warning("Cannot initialize OTA - not connected to AWS IoT")
                return False
        else:
            logger.warning("No OTA manager available")
            return False
