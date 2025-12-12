"""
Version Management Utility

Provides centralized version information by reading from package metadata
or pyproject.toml as a fallback. This ensures a single source of truth
for the firmware version reported to AWS IoT and other services.
"""

import sys
from pathlib import Path
from typing import Optional


def get_version() -> str:
    """
    Get the current package version.
    
    First attempts to read from installed package metadata (via importlib.metadata),
    then falls back to reading directly from pyproject.toml.
    
    Returns:
        str: The current version string (e.g., "6.1.13")
    """
    # Try to get version from installed package metadata
    try:
        if sys.version_info >= (3, 8):
            from importlib.metadata import version, PackageNotFoundError
        else:
            from importlib_metadata import version, PackageNotFoundError
        
        try:
            return version("tsv6")
        except PackageNotFoundError:
            pass  # Fall through to pyproject.toml method
    except ImportError:
        pass  # Fall through to pyproject.toml method
    
    # Fallback: Read version directly from pyproject.toml
    return _read_version_from_pyproject()


def _read_version_from_pyproject() -> str:
    """
    Read version from pyproject.toml file.
    
    Returns:
        str: The version string from pyproject.toml, or "0.0.0" if not found
    """
    try:
        # Find pyproject.toml - it should be 4 levels up from this file
        # Path structure: src/tsv6/utils/version.py -> ../../../../pyproject.toml
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent.parent
        pyproject_path = project_root / "pyproject.toml"
        
        if not pyproject_path.exists():
            # Try alternative search by walking up until we find it
            search_path = current_file.parent
            for _ in range(10):  # Limit search depth
                test_path = search_path / "pyproject.toml"
                if test_path.exists():
                    pyproject_path = test_path
                    break
                search_path = search_path.parent
                if search_path == search_path.parent:  # Reached root
                    break
        
        if pyproject_path.exists():
            with open(pyproject_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line.startswith('version = '):
                        # Extract version: version = "6.1.13"
                        version_str = line.split('=')[1].strip()
                        # Remove quotes
                        version_str = version_str.strip('"').strip("'")
                        return version_str
    except Exception as e:
        print(f"Warning: Could not read version from pyproject.toml: {e}")
    
    return "0.0.0"  # Default fallback


def get_firmware_version() -> str:
    """
    Get the firmware version string.
    
    This is an alias for get_version() to maintain compatibility
    with existing code that expects a firmware version.
    
    Returns:
        str: The current firmware version
    """
    return get_version()


# Module-level version constant for convenience
__version__ = get_version()


if __name__ == "__main__":
    # Allow testing the version utility directly
    print(f"TSV6 Version: {get_version()}")
    print(f"Firmware Version: {get_firmware_version()}")
