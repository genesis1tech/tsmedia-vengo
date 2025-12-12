#!/usr/bin/env python3
"""
Display detection and setup utility for TSV6 Video Player
"""

import os
import subprocess
import tkinter as tk

def detect_and_set_display():
    """Detect and set the DISPLAY environment variable if not set"""
    if os.environ.get('DISPLAY'):
        print(f"✓ DISPLAY already set: {os.environ.get('DISPLAY')}")
        return True
    
    # Check for X11 sockets
    x11_sockets = []
    if os.path.exists('/tmp/.X11-unix'):
        for socket in os.listdir('/tmp/.X11-unix'):
            if socket.startswith('X'):
                display_num = socket[1:]  # Remove 'X' prefix
                x11_sockets.append(f":{display_num}")
    
    # Try each available display
    for display in x11_sockets:
        try:
            # Test if the display works
            env = os.environ.copy()
            env['DISPLAY'] = display
            result = subprocess.run(['xset', 'q'], 
                                  env=env, 
                                  capture_output=True, 
                                  timeout=5)
            if result.returncode == 0:
                os.environ['DISPLAY'] = display
                print(f"✓ DISPLAY set to: {display}")
                return True
        except (subprocess.TimeoutExpired, FileNotFoundError):
            continue
    
    # Try default displays
    for display in [':0', ':1']:
        try:
            env = os.environ.copy()
            env['DISPLAY'] = display
            # Try a simple tkinter test
            import tkinter as tk
            test_root = tk.Tk()
            test_root.withdraw()  # Hide the window
            test_root.destroy()
            os.environ['DISPLAY'] = display
            print(f"✓ DISPLAY set to: {display} (via Tkinter test)")
            return True
        except Exception:
            continue
    
    print("⚠ Could not detect working display")
    return False

def setup_video_display_safe(self):
    """Set up the video display window with error handling"""
    
    # Try to detect and set display first
    if not detect_and_set_display():
        print("❌ No display available - cannot create GUI")
        return False
    
    try:
        self.root = tk.Tk()
        self.root.title("TSV6 Enhanced Video Player with Barcode Scanning")
        self.root.configure(bg='black')
        
        # Configure window
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_width}x{screen_height}+0+0")
        
        # Make fullscreen
        self.root.attributes('-fullscreen', True)
        self.root.config(cursor="none")
        
        # Create canvas for video
        self.canvas = tk.Canvas(
            self.root, 
            bg='black', 
            highlightthickness=0,
            width=screen_width,
            height=screen_height
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        
        print("✓ Video display setup complete")
        return True
        
    except Exception as e:
        print(f"❌ Failed to setup video display: {e}")
        return False

if __name__ == "__main__":
    # Test the display detection
    detect_and_set_display()
