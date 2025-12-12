import time
import threading
import uuid
import platform
import psutil
from typing import Optional, Callable

# Import version utility for dynamic version management
try:
    from tsv6.utils.version import get_firmware_version
except ImportError:
    # Fallback if version module not available
    def get_firmware_version():
        return "6.0.0"

class DeviceManager:
    def __init__(self, device_location: str = "Demo Unit"):
        self.device_location = device_location
        self.firmware_version = get_firmware_version()
        self.device_type = "Topper Stopper Python"
        self.device_client = "Genesis 1 Technologies LLC"
        self.start_time = time.time()
        
        # Device ID generation
        self._device_id = self._generate_device_id()
        
    def _generate_device_id(self) -> str:
        """Generate unique device ID"""
        import hashlib
        system_info = f"{platform.node()}-{platform.machine()}-{platform.processor()}"
        device_id = hashlib.md5(system_info.encode()).hexdigest()[:16].upper()
        return device_id
        
    def generate_transaction_id(self) -> str:
        """Generate UUID-like transaction ID"""
        return str(uuid.uuid4())
        
    def get_system_status(self) -> dict:
        """Get current system status"""
        try:
            # CPU temperature (Raspberry Pi specific)
            cpu_temp = self._get_cpu_temperature()
            
            # Memory usage
            memory = psutil.virtual_memory()
            
            # Disk usage
            disk = psutil.disk_usage('/')
            
            # Network info (if available)
            network_info = self._get_network_info()
            
            return {
                "deviceID": self._device_id,
                "firmwareVersion": self.firmware_version,
                "deviceType": self.device_type,
                "deviceClient": self.device_client,
                "deviceLocation": self.device_location,
                "temperature": cpu_temp,
                "memoryUsage": memory.percent,
                "memoryTotal": memory.total,
                "memoryAvailable": memory.available,
                "diskUsage": (disk.used / disk.total) * 100,
                "diskTotal": disk.total,
                "diskFree": disk.free,
                "timeConnected": int(time.time() - self.start_time),
                "uptime": int(time.time() - self.start_time),
                **network_info
            }
            
        except Exception as e:
            print(f"Error getting system status: {e}")
            return {
                "deviceID": self._device_id,
                "firmwareVersion": self.firmware_version,
                "error": str(e)
            }
            
    def _get_cpu_temperature(self) -> float:
        """Get CPU temperature (Raspberry Pi optimized)"""
        try:
            # Try Raspberry Pi method first
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp_c = float(f.read()) / 1000.0
                return (temp_c * 9/5) + 32  # Convert to Fahrenheit
        except:
            # Fallback for other systems
            try:
                temps = psutil.sensors_temperatures()
                if temps:
                    for name, entries in temps.items():
                        for entry in entries:
                            if entry.current:
                                return (entry.current * 9/5) + 32
            except:
                pass
        return 70.0  # Default temperature
        
    def _get_network_info(self) -> dict:
        """Get network information"""
        try:
            import socket
            hostname = socket.gethostname()
            local_ip = socket.gethostbyname(hostname)
            
            # Try to get more detailed network info
            network_info = {
                "hostname": hostname,
                "localIP": local_ip
            }
            
            # Get network interface stats if available
            try:
                net_io = psutil.net_io_counters()
                network_info.update({
                    "bytesReceived": net_io.bytes_recv,
                    "bytesSent": net_io.bytes_sent,
                    "packetsReceived": net_io.packets_recv,
                    "packetsSent": net_io.packets_sent
                })
            except:
                pass
                
            return network_info
            
        except Exception as e:
            return {"networkError": str(e)}