#!/usr/bin/env python3
"""
Launch script for TSV6 Production Video Player

This script launches the production-ready version with all monitoring
and resilience features enabled.
"""

import sys
import os
from pathlib import Path

# Add src directory to Python path
src_path = Path(__file__).parent / "src"
sys.path.insert(0, str(src_path))

if __name__ == "__main__":
    try:
        from tsv6.core.production_main import main as production_main
        production_main()
        
    except ImportError as e:
        print(f"❌ Import error: {e}")
        print("Make sure all dependencies are installed:")
        print("  pip install awsiotsdk psutil")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n🛑 Application interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
