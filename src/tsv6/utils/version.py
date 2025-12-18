"""
Version Management Utility

Provides centralized version information by reading from git tags first,
then falling back to package metadata or pyproject.toml. This ensures the
firmware version reported to AWS IoT matches the GitHub release version.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional


def _get_version_from_git() -> Optional[str]:
    """
    Get version from git tags.

    Returns:
        str: The version from git tag (e.g., "v6.3.0-4g-lte"), or None if not available
    """
    try:
        # Find the project root (where .git is located)
        current_file = Path(__file__)
        project_root = current_file.parent.parent.parent.parent

        # Try to get the most recent tag
        result = subprocess.run(
            ['git', 'describe', '--tags', '--abbrev=0'],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            version = result.stdout.strip()
            if version:
                return version

    except (subprocess.TimeoutExpired, FileNotFoundError, Exception):
        pass  # Git not available or other error

    return None


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


def get_version() -> str:
    """
    Get the current package version.

    Priority order:
    1. Git tag (for matching GitHub release version)
    2. Installed package metadata (via importlib.metadata)
    3. pyproject.toml as fallback

    Returns:
        str: The current version string (e.g., "v6.3.0-4g-lte")
    """
    # First, try to get version from git tag (highest priority)
    git_version = _get_version_from_git()
    if git_version:
        return git_version

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


def get_firmware_version() -> str:
    """
    Get the firmware version string.

    This is an alias for get_version() to maintain compatibility
    with existing code that expects a firmware version.

    Returns:
        str: The current firmware version (e.g., "v6.3.0-4g-lte")
    """
    return get_version()


# Module-level version constant for convenience
__version__ = get_version()


if __name__ == "__main__":
    # Allow testing the version utility directly
    print(f"TSV6 Version: {get_version()}")
    print(f"Firmware Version: {get_firmware_version()}")

    # Show source for debugging
    git_ver = _get_version_from_git()
    pyproject_ver = _read_version_from_pyproject()
    print(f"\nVersion sources:")
    print(f"  Git tag: {git_ver or 'not available'}")
    print(f"  pyproject.toml: {pyproject_ver}")
