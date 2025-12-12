#!/usr/bin/env python3
"""
Test script for AnimatedSleepDisplay
Runs the animation for 10 seconds to verify visuals.
"""

import sys
import time
import logging
from pathlib import Path
from multiprocessing import Process, Event

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.tsv6.utils.sleep_display import AnimatedSleepDisplay

def run_animation(stop_event):
    print("Starting animation process...")
    try:
        display = AnimatedSleepDisplay(fullscreen=False)
        display.run("7:30 AM", stop_event)
    except Exception as e:
        print(f"Error: {e}")

def main():
    logging.basicConfig(level=logging.INFO)
    print("Initializing test...")
    
    stop_event = Event()
    p = Process(target=run_animation, args=(stop_event,))
    p.start()
    
    print("Animation running for 10 seconds...")
    time.sleep(10)
    
    print("Stopping animation...")
    stop_event.set()
    p.join(timeout=2)
    
    if p.is_alive():
        print("Force terminating...")
        p.terminate()
        
    print("Test complete.")

if __name__ == "__main__":
    main()
