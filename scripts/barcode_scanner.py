#!/usr/bin/env python3
"""
Webcam Barcode and QR Code Scanner for Recyclable Items
Detects barcodes and QR codes in real-time from webcam feed
"""

import cv2
import numpy as np
from pyzbar import pyzbar
import datetime
import json
import time
from typing import List, Dict, Any

class BarcodeScanner:
    def __init__(self, camera_index: int = 0, save_scans: bool = True):
        """
        Initialize the barcode scanner
        
        Args:
            camera_index: Camera device index (usually 0 for first camera)
            save_scans: Whether to save scan results to a JSON file
        """
        self.camera_index = camera_index
        self.save_scans = save_scans
        self.scan_history = []
        self.last_detected_codes = set()
        self.detection_cooldown = 2.0  # seconds to wait before detecting same code again
        self.last_detection_time = {}
        
        # Initialize camera
        self.cap = cv2.VideoCapture(camera_index)
        if not self.cap.isOpened():
            raise RuntimeError(f"Could not open camera at index {camera_index}")
        
        # Force MJPEG format to avoid h264_v4l2m2m decoder issues
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc('M','J','P','G'))
        
        # Set camera properties for better performance
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)
        
        print("Barcode Scanner initialized successfully!")
        print("Press 'q' to quit, 's' to save current scan history")
        print("Position barcode/QR code clearly in front of camera")

    def detect_codes(self, frame: np.ndarray) -> List[Dict[str, Any]]:
        """
        Detect barcodes and QR codes in a frame
        
        Args:
            frame: Input image frame
            
        Returns:
            List of detected codes with their information
        """
        # Convert frame to grayscale for better detection
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        
        # Detect barcodes and QR codes
        detected_codes = pyzbar.decode(gray)
        
        results = []
        current_time = time.time()
        
        for code in detected_codes:
            # Extract code data
            code_data = code.data.decode('utf-8')
            code_type = code.type
            
            # Check if we've detected this code recently (cooldown period)
            if (code_data in self.last_detection_time and 
                current_time - self.last_detection_time[code_data] < self.detection_cooldown):
                continue
            
            # Update last detection time
            self.last_detection_time[code_data] = current_time
            
            # Get bounding box coordinates
            (x, y, w, h) = code.rect
            
            # Create result dictionary
            result = {
                'data': code_data,
                'type': code_type,
                'timestamp': datetime.datetime.now().isoformat(),
                'bbox': {'x': x, 'y': y, 'width': w, 'height': h}
            }
            
            results.append(result)
            
            # Add to scan history if saving is enabled
            if self.save_scans:
                self.scan_history.append(result)
                print(f"📱 Detected {code_type}: {code_data}")
                
                # Try to identify if it's a recyclable item
                self.analyze_recyclable_item(code_data, code_type)
        
        return results

    def analyze_recyclable_item(self, code_data: str, code_type: str):
        """
        Analyze the scanned code to determine if it's a recyclable item
        This is a basic implementation - you can expand this with a database lookup
        
        Args:
            code_data: The scanned barcode/QR code data
            code_type: Type of code (CODE128, EAN13, QRCODE, etc.)
        """
        # Basic analysis based on code patterns
        recyclable_info = {
            'is_likely_recyclable': False,
            'material_type': 'Unknown',
            'recycling_notes': ''
        }
        
        # UPC/EAN codes (common on consumer products)
        if code_type in ['EAN13', 'EAN8', 'UPCA', 'UPCE']:
            recyclable_info['is_likely_recyclable'] = True
            recyclable_info['material_type'] = 'Consumer Product'
            recyclable_info['recycling_notes'] = 'Check local recycling guidelines for packaging'
            
        # Code 128 (often used on packaging)
        elif code_type == 'CODE128':
            recyclable_info['is_likely_recyclable'] = True
            recyclable_info['material_type'] = 'Packaging/Industrial'
            recyclable_info['recycling_notes'] = 'Likely packaging material - check recycling symbols'
            
        # QR codes might contain recycling information
        elif code_type == 'QRCODE':
            if any(keyword in code_data.lower() for keyword in ['recycle', 'green', 'eco', 'sustainable']):
                recyclable_info['is_likely_recyclable'] = True
                recyclable_info['material_type'] = 'Product with recycling info'
                recyclable_info['recycling_notes'] = 'Contains recycling-related information'
        
        if recyclable_info['is_likely_recyclable']:
            print(f"♻️  Recyclable item detected!")
            print(f"   Material: {recyclable_info['material_type']}")
            print(f"   Notes: {recyclable_info['recycling_notes']}")
        else:
            print(f"ℹ️  Item scanned but recycling status unknown")

    def draw_detections(self, frame: np.ndarray, detections: List[Dict[str, Any]]) -> np.ndarray:
        """
        Draw bounding boxes and labels for detected codes
        
        Args:
            frame: Input frame
            detections: List of detected codes
            
        Returns:
            Frame with drawn detections
        """
        for detection in detections:
            bbox = detection['bbox']
            x, y, w, h = bbox['x'], bbox['y'], bbox['width'], bbox['height']
            
            # Draw bounding box
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            
            # Draw label
            label = f"{detection['type']}: {detection['data'][:20]}..."
            label_size = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 2)[0]
            cv2.rectangle(frame, (x, y - label_size[1] - 10), 
                         (x + label_size[0], y), (0, 255, 0), -1)
            cv2.putText(frame, label, (x, y - 5), 
                       cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 2)
        
        return frame

    def save_scan_history(self, filename: str = None):
        """
        Save scan history to JSON file
        
        Args:
            filename: Output filename (optional)
        """
        if not self.scan_history:
            print("No scans to save!")
            return
            
        if filename is None:
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"barcode_scans_{timestamp}.json"
        
        try:
            with open(filename, 'w') as f:
                json.dump(self.scan_history, f, indent=2)
            print(f"✅ Saved {len(self.scan_history)} scans to {filename}")
        except Exception as e:
            print(f"❌ Error saving scan history: {e}")

    def run(self):
        """
        Main scanning loop
        """
        print("\n🎯 Starting barcode scanner...")
        print("Position items in front of the camera to scan")
        print("Press 'q' to quit, 's' to save scan history")
        
        try:
            while True:
                # Capture frame
                ret, frame = self.cap.read()
                if not ret:
                    print("❌ Failed to capture frame")
                    break
                
                # Detect codes
                detections = self.detect_codes(frame)
                
                # Draw detections on frame
                display_frame = self.draw_detections(frame.copy(), detections)
                
                # Add instructions to frame
                instructions = [
                    "Barcode/QR Code Scanner for Recyclables",
                    "Press 'q' to quit, 's' to save history",
                    f"Scans captured: {len(self.scan_history)}"
                ]
                
                for i, instruction in enumerate(instructions):
                    cv2.putText(display_frame, instruction, (10, 30 + i * 25),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                    cv2.putText(display_frame, instruction, (10, 30 + i * 25),
                               cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 1)
                
                # Display frame
                cv2.imshow('Barcode Scanner', display_frame)
                
                # Handle key presses
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    break
                elif key == ord('s'):
                    self.save_scan_history()
                
        except KeyboardInterrupt:
            print("\n⏹️  Stopping scanner...")
        
        finally:
            self.cleanup()

    def cleanup(self):
        """
        Clean up resources
        """
        if self.cap:
            self.cap.release()
        cv2.destroyAllWindows()
        
        # Auto-save scan history if we have scans
        if self.save_scans and self.scan_history:
            print(f"\n💾 Auto-saving {len(self.scan_history)} scans...")
            self.save_scan_history()


def main():
    """
    Main function to run the barcode scanner
    """
    try:
        # Initialize scanner (try camera 0 first, then 1 if that fails)
        scanner = None
        for camera_idx in [0, 1]:
            try:
                scanner = BarcodeScanner(camera_index=camera_idx, save_scans=True)
                print(f"✅ Successfully connected to camera {camera_idx}")
                break
            except RuntimeError:
                print(f"⚠️  Camera {camera_idx} not available, trying next...")
                continue
        
        if scanner is None:
            print("❌ No cameras available!")
            return
        
        # Run the scanner
        scanner.run()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        if scanner:
            scanner.cleanup()


if __name__ == "__main__":
    main()
