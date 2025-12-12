#!/usr/bin/env python3
"""
Network Diagnostics Utility for AWS IoT Connections

Provides comprehensive network testing and diagnostics for AWS IoT connectivity issues.
"""

import socket
import ssl
import subprocess
import time
import logging
from typing import Dict, List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class NetworkDiagnostics:
    """Network diagnostics utility for AWS IoT connections"""
    
    def __init__(self, endpoint: str, port: int = 8883):
        self.endpoint = endpoint
        self.port = port
        self.host = endpoint.split('-ats')[0] + '-ats.iot.us-east-1.amazonaws.com'
    
    def run_full_diagnostics(self) -> Dict[str, any]:
        """Run comprehensive network diagnostics"""
        results = {
            "timestamp": time.time(),
            "endpoint": self.endpoint,
            "host": self.host,
            "port": self.port,
            "tests": {}
        }
        
        # Test DNS resolution
        results["tests"]["dns_resolution"] = self._test_dns_resolution()
        
        # Test basic connectivity
        results["tests"]["basic_connectivity"] = self._test_basic_connectivity()
        
        # Test SSL/TLS connection
        results["tests"]["ssl_connection"] = self._test_ssl_connection()
        
        # Test MQTT-specific connectivity
        results["tests"]["mqtt_connectivity"] = self._test_mqtt_connectivity()
        
        # Test network stability
        results["tests"]["network_stability"] = self._test_network_stability()
        
        # Generate summary
        results["summary"] = self._generate_summary(results["tests"])
        
        return results
    
    def _test_dns_resolution(self) -> Dict[str, any]:
        """Test DNS resolution for the endpoint"""
        result = {
            "test_name": "DNS Resolution",
            "status": "running",
            "details": {}
        }
        
        try:
            start_time = time.time()
            addresses = socket.getaddrinfo(self.host, self.port)
            resolve_time = time.time() - start_time
            
            ip_addresses = [addr[4][0] for addr in addresses]
            
            result.update({
                "status": "passed",
                "details": {
                    "resolve_time_ms": round(resolve_time * 1000, 2),
                    "ip_addresses": ip_addresses,
                    "address_count": len(ip_addresses)
                }
            })
            
        except socket.gaierror as e:
            result.update({
                "status": "failed",
                "error": str(e),
                "details": {"error_type": "DNS_RESOLUTION_FAILED"}
            })
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e),
                "details": {"error_type": "UNEXPECTED_ERROR"}
            })
        
        return result
    
    def _test_basic_connectivity(self) -> Dict[str, any]:
        """Test basic TCP connectivity to the endpoint"""
        result = {
            "test_name": "Basic TCP Connectivity",
            "status": "running",
            "details": {}
        }
        
        try:
            start_time = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            connect_result = sock.connect_ex((self.host, self.port))
            connect_time = time.time() - start_time
            
            sock.close()
            
            if connect_result == 0:
                result.update({
                    "status": "passed",
                    "details": {
                        "connect_time_ms": round(connect_time * 1000, 2),
                        "connection_result": connect_result
                    }
                })
            else:
                result.update({
                    "status": "failed",
                    "error": f"TCP connection failed with code {connect_result}",
                    "details": {
                        "connection_result": connect_result,
                        "error_type": "TCP_CONNECTION_FAILED"
                    }
                })
                
        except socket.timeout:
            result.update({
                "status": "failed",
                "error": "Connection timeout",
                "details": {"error_type": "CONNECTION_TIMEOUT"}
            })
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e),
                "details": {"error_type": "UNEXPECTED_ERROR"}
            })
        
        return result
    
    def _test_ssl_connection(self) -> Dict[str, any]:
        """Test SSL/TLS connection to the endpoint"""
        result = {
            "test_name": "SSL/TLS Connection",
            "status": "running",
            "details": {}
        }
        
        try:
            start_time = time.time()
            
            # Create SSL context
            context = ssl.create_default_context()
            context.check_hostname = True
            context.verify_mode = ssl.CERT_REQUIRED
            
            # Connect with SSL
            with socket.create_connection((self.host, self.port), timeout=10) as sock:
                with context.wrap_socket(sock, server_hostname=self.host) as ssock:
                    ssl_connect_time = time.time() - start_time
                    
                    # Get certificate info
                    cert = ssock.getpeercert()
                    cipher = ssock.cipher()
                    
                    result.update({
                        "status": "passed",
                        "details": {
                            "ssl_connect_time_ms": round(ssl_connect_time * 1000, 2),
                            "certificate_subject": cert.get("subject"),
                            "certificate_issuer": cert.get("issuer"),
                            "certificate_version": cert.get("version"),
                            "cipher_name": cipher[0] if cipher else None,
                            "cipher_version": cipher[1] if cipher else None,
                            "cipher_bits": cipher[2] if cipher else None
                        }
                    })
                    
        except ssl.SSLCertVerificationError as e:
            result.update({
                "status": "failed",
                "error": f"SSL certificate verification failed: {e}",
                "details": {"error_type": "SSL_CERT_VERIFICATION_FAILED"}
            })
        except ssl.SSLError as e:
            result.update({
                "status": "failed",
                "error": f"SSL error: {e}",
                "details": {"error_type": "SSL_ERROR"}
            })
        except socket.timeout:
            result.update({
                "status": "failed",
                "error": "SSL connection timeout",
                "details": {"error_type": "SSL_CONNECTION_TIMEOUT"}
            })
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e),
                "details": {"error_type": "UNEXPECTED_ERROR"}
            })
        
        return result
    
    def _test_mqtt_connectivity(self) -> Dict[str, any]:
        """Test MQTT-specific connectivity"""
        result = {
            "test_name": "MQTT Connectivity",
            "status": "running",
            "details": {}
        }
        
        try:
            # Test if MQTT port is accessible
            start_time = time.time()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            
            connect_result = sock.connect_ex((self.host, self.port))
            connect_time = time.time() - start_time
            
            sock.close()
            
            if connect_result == 0:
                result.update({
                    "status": "passed",
                    "details": {
                        "mqtt_connect_time_ms": round(connect_time * 1000, 2),
                        "mqtt_port_accessible": True,
                        "connection_result": connect_result
                    }
                })
            else:
                result.update({
                    "status": "failed",
                    "error": f"MQTT port not accessible: {connect_result}",
                    "details": {
                        "mqtt_port_accessible": False,
                        "connection_result": connect_result,
                        "error_type": "MQTT_PORT_NOT_ACCESSIBLE"
                    }
                })
                
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e),
                "details": {"error_type": "UNEXPECTED_ERROR"}
            })
        
        return result
    
    def _test_network_stability(self) -> Dict[str, any]:
        """Test network stability with multiple connection attempts"""
        result = {
            "test_name": "Network Stability",
            "status": "running",
            "details": {}
        }
        
        try:
            attempts = 5
            successful_attempts = 0
            connection_times = []
            errors = []
            
            for i in range(attempts):
                try:
                    start_time = time.time()
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(5)
                    
                    connect_result = sock.connect_ex((self.host, self.port))
                    connect_time = time.time() - start_time
                    
                    sock.close()
                    
                    if connect_result == 0:
                        successful_attempts += 1
                        connection_times.append(round(connect_time * 1000, 2))
                    else:
                        errors.append(f"Attempt {i+1}: Connection failed with code {connect_result}")
                    
                    time.sleep(1)  # Small delay between attempts
                    
                except Exception as e:
                    errors.append(f"Attempt {i+1}: {str(e)}")
            
            success_rate = (successful_attempts / attempts) * 100
            
            if success_rate >= 80:
                status = "passed"
            elif success_rate >= 60:
                status = "warning"
            else:
                status = "failed"
            
            avg_connect_time = sum(connection_times) / len(connection_times) if connection_times else 0
            
            result.update({
                "status": status,
                "details": {
                    "attempts": attempts,
                    "successful_attempts": successful_attempts,
                    "success_rate_percent": round(success_rate, 1),
                    "avg_connect_time_ms": round(avg_connect_time, 2),
                    "connection_times_ms": connection_times,
                    "errors": errors
                }
            })
            
        except Exception as e:
            result.update({
                "status": "error",
                "error": str(e),
                "details": {"error_type": "UNEXPECTED_ERROR"}
            })
        
        return result
    
    def _generate_summary(self, test_results: Dict[str, Dict]) -> Dict[str, any]:
        """Generate a summary of all test results"""
        summary = {
            "overall_status": "unknown",
            "passed_tests": 0,
            "failed_tests": 0,
            "error_tests": 0,
            "warning_tests": 0,
            "recommendations": []
        }
        
        for test_name, test_result in test_results.items():
            status = test_result.get("status", "unknown")
            
            if status == "passed":
                summary["passed_tests"] += 1
            elif status == "failed":
                summary["failed_tests"] += 1
            elif status == "error":
                summary["error_tests"] += 1
            elif status == "warning":
                summary["warning_tests"] += 1
        
        # Determine overall status
        total_tests = summary["passed_tests"] + summary["failed_tests"] + summary["error_tests"]
        
        if total_tests == 0:
            summary["overall_status"] = "error"
        elif summary["failed_tests"] == 0 and summary["error_tests"] == 0:
            summary["overall_status"] = "passed"
        elif summary["failed_tests"] <= 1 and summary["error_tests"] == 0:
            summary["overall_status"] = "warning"
        else:
            summary["overall_status"] = "failed"
        
        # Generate recommendations
        if test_results.get("dns_resolution", {}).get("status") == "failed":
            summary["recommendations"].append("Check DNS configuration and internet connectivity")
        
        if test_results.get("basic_connectivity", {}).get("status") == "failed":
            summary["recommendations"].append("Check network connectivity and firewall settings")
        
        if test_results.get("ssl_connection", {}).get("status") == "failed":
            summary["recommendations"].append("Check SSL/TLS configuration and certificate validity")
        
        if test_results.get("mqtt_connectivity", {}).get("status") == "failed":
            summary["recommendations"].append("Verify MQTT port (8883) is not blocked by firewall")
        
        if test_results.get("network_stability", {}).get("status") in ["failed", "warning"]:
            summary["recommendations"].append("Network stability issues detected - check connection quality")
        
        return summary
    
    def print_diagnostics(self, results: Dict[str, any]):
        """Print formatted diagnostics results"""
        logger.info(f"\n{'='*60}")
        logger.info(f"Network Diagnostics for {results['endpoint']}")
        logger.info(f"{'='*60}")

        for test_name, test_result in results["tests"].items():
            status_icon = {
                "passed": "✅",
                "failed": "❌",
                "error": "⚠️",
                "warning": "⚠️",
                "running": "🔄"
            }.get(test_result.get("status"), "❓")

            logger.info(f"\n{status_icon} {test_result.get('test_name', test_name)}")
            logger.info(f"   Status: {test_result.get('status', 'unknown')}")

            if test_result.get("error"):
                logger.info(f"   Error: {test_result['error']}")

            # Print key details
            details = test_result.get("details", {})
            if "resolve_time_ms" in details:
                logger.info(f"   DNS Resolution: {details['resolve_time_ms']}ms")
            if "connect_time_ms" in details:
                logger.info(f"   Connect Time: {details['connect_time_ms']}ms")
            if "ssl_connect_time_ms" in details:
                logger.info(f"   SSL Connect Time: {details['ssl_connect_time_ms']}ms")
            if "success_rate_percent" in details:
                logger.info(f"   Stability: {details['success_rate_percent']}% success rate")

        # Print summary
        summary = results["summary"]
        status_icon = {
            "passed": "✅",
            "failed": "❌",
            "warning": "⚠️",
            "error": "❌"
        }.get(summary["overall_status"], "❓")

        logger.info(f"\n{status_icon} Overall Status: {summary['overall_status'].upper()}")
        logger.info(f"   Passed: {summary['passed_tests']} | Failed: {summary['failed_tests']} | Errors: {summary['error_tests']}")

        if summary["recommendations"]:
            logger.info("\n📋 Recommendations:")
            for i, rec in enumerate(summary["recommendations"], 1):
                logger.info(f"   {i}. {rec}")

        logger.info(f"{'='*60}")


def run_quick_connectivity_test(endpoint: str) -> bool:
    """Run a quick connectivity test for AWS IoT endpoint"""
    try:
        diagnostics = NetworkDiagnostics(endpoint)
        results = diagnostics.run_full_diagnostics()

        # Print results
        diagnostics.print_diagnostics(results)

        # Return True if overall status is passed or warning
        return results["summary"]["overall_status"] in ["passed", "warning"]

    except Exception as e:
        logger.error(f"Diagnostics failed: {e}")
        return False


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        endpoint = sys.argv[1]
        logger.info(f"Testing connectivity to: {endpoint}")
        run_quick_connectivity_test(endpoint)
    else:
        logger.info("Usage: python network_diagnostics.py <aws_iot_endpoint>")