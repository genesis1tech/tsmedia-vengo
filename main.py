#!/usr/bin/env python3
"""
TSV6 Video Player - Main Entry Point

This is the main entry point for the TSV6 Enhanced Video Player application.
The actual application logic is in src/tsv6/core/main.py
"""

import sys
import os
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

# Import and run the main application
if __name__ == "__main__":
    try:
        from tsv6.core.main import main as run_main
        
        # Create and run the application
        # Run the main application
        run_main()
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure all dependencies are installed and the src directory structure is correct.")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n🛑 Application interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
