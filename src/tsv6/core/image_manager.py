#!/usr/bin/env python3
from __future__ import annotations
"""
Image Manager for TSV6
Handles downloading, caching, and displaying product images from URLs
"""

import os
import time
import threading
import hashlib
from pathlib import Path
from typing import Optional, Callable
import requests

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠ PIL not available - image display will be disabled")

class ImageManager:
    """Manages product image downloading, caching, and display"""
    
    def __init__(self, cache_dir: str = "image_cache", max_cache_size_mb: int = 100):
        """
        Initialize image manager
        
        Args:
            cache_dir: Directory for cached images
            max_cache_size_mb: Maximum cache size in MB
        """
        self.cache_dir = Path(cache_dir)
        self.max_cache_size = max_cache_size_mb * 1024 * 1024  # Convert to bytes
        self.download_timeout = 10  # seconds
        
        # Create cache directory
        self.cache_dir.mkdir(exist_ok=True)
        
        # Active downloads tracking
        self.active_downloads = {}
        self.download_lock = threading.Lock()
        
        print(f"✅ Image Manager initialized (cache: {cache_dir}, max: {max_cache_size_mb}MB)")
    
    def _get_cache_filename(self, url: str) -> Path:
        """Generate cache filename from URL"""
        # Create hash of URL for filename
        url_hash = hashlib.md5(url.encode()).hexdigest()
        # Try to get file extension from URL
        extension = '.jpg'  # Default
        if url.lower().endswith(('.png', '.jpeg', '.jpg', '.gif', '.webp')):
            extension = '.' + url.split('.')[-1].lower()
        
        return self.cache_dir / f"{url_hash}{extension}"
    
    def _cleanup_cache(self):
        """Remove old cache files if cache is too large"""
        try:
            # Get all cache files with their sizes and modification times
            cache_files = []
            total_size = 0
            
            for file_path in self.cache_dir.glob('*'):
                if file_path.is_file():
                    stat = file_path.stat()
                    cache_files.append((file_path, stat.st_size, stat.st_mtime))
                    total_size += stat.st_size
            
            # If cache is too large, remove oldest files
            if total_size > self.max_cache_size:
                # Sort by modification time (oldest first)
                cache_files.sort(key=lambda x: x[2])
                
                removed_size = 0
                for file_path, size, _ in cache_files:
                    file_path.unlink()
                    removed_size += size
                    print(f"🗑️ Removed cached image: {file_path.name}")
                    
                    # Stop when we've freed enough space
                    if total_size - removed_size < self.max_cache_size * 0.8:  # 80% of max
                        break
                        
        except Exception as e:
            print(f"⚠ Cache cleanup error: {e}")
    
    def download_image(self, url: str, callback: Optional[Callable] = None) -> Optional[Path]:
        """
        Download image from URL (async with callback)
        
        Args:
            url: Image URL to download
            callback: Function to call when download completes (path, success)
            
        Returns:
            Path to cached file if successful, None otherwise
        """
        cache_path = self._get_cache_filename(url)
        
        # Check if already cached
        if cache_path.exists():
            if callback:
                callback(cache_path, True)
            return cache_path
        
        # Check if already downloading
        with self.download_lock:
            if url in self.active_downloads:
                print(f"📥 Already downloading: {url}")
                return None
            self.active_downloads[url] = True
        
        def download_worker():
            """Background download worker"""
            success = False
            result_cache_path = cache_path  # Initialize result path
            try:
                print(f"📥 Downloading image: {url}")
                
                # Download with timeout
                response = requests.get(
                    url, 
                    timeout=self.download_timeout,
                    headers={'User-Agent': 'TSV6-ImageManager/1.0'}
                )
                response.raise_for_status()
                
                # Save to cache
                with open(cache_path, 'wb') as f:
                    f.write(response.content)
                
                print(f"✅ Image cached: {cache_path.name}")
                success = True
                
                # Cleanup old cache files if needed
                self._cleanup_cache()
                
            except Exception as e:
                print(f"❌ Image download failed: {e}")
                # Remove partial file if exists
                if cache_path.exists():
                    cache_path.unlink()
                result_cache_path = None
            
            finally:
                # Remove from active downloads
                with self.download_lock:
                    self.active_downloads.pop(url, None)
                
                # Call callback
                if callback:
                    callback(result_cache_path, success)
        
        # Start download in background thread
        download_thread = threading.Thread(target=download_worker, name="ImageDownloader")
        download_thread.daemon = True
        download_thread.start()
        
        return None  # Will be provided via callback
    
    def load_image_for_display(self, image_path: Path, target_size: tuple = (400, 400), maintain_aspect_ratio: bool = True, master=None) -> Optional["ImageTk.PhotoImage"]:
        """
        Load and resize image for tkinter display

        Args:
            image_path: Path to image file
            target_size: Target size (width, height)
            maintain_aspect_ratio: If True, maintain aspect ratio (may have white space).
                                   If False, force exact size (may stretch image)
            master: Tk master widget to associate the PhotoImage with

        Returns:
            ImageTk.PhotoImage object or None
        """
        if not PIL_AVAILABLE:
            print("⚠ PIL not available - cannot load image")
            return None

        try:
            # Open image - don't use context manager as we need PIL image
            # to persist until PhotoImage is rendered by Tkinter
            img = Image.open(image_path)

            # Convert to RGB if needed (creates a copy)
            if img.mode != 'RGB':
                img = img.convert('RGB')

            if maintain_aspect_ratio:
                # Calculate size maintaining aspect ratio
                img.thumbnail(target_size, Image.Resampling.LANCZOS)
            else:
                # Force exact size (may stretch image to fill entire area)
                img = img.resize(target_size, Image.Resampling.LANCZOS)

            # Create tkinter-compatible image with explicit master
            photo = ImageTk.PhotoImage(img, master=master)

            # CRITICAL: Keep reference to PIL image to prevent garbage collection
            # Without this, the underlying image data can be freed before Tkinter renders
            photo._pil_image = img

            return photo

        except Exception as e:
            print(f"❌ Failed to load image {image_path}: {e}")
            return None
    
    def get_cache_stats(self) -> dict:
        """Get cache statistics"""
        try:
            files = list(self.cache_dir.glob('*'))
            total_size = sum(f.stat().st_size for f in files if f.is_file())
            
            return {
                'cache_dir': str(self.cache_dir),
                'file_count': len(files),
                'total_size_mb': round(total_size / 1024 / 1024, 2),
                'max_size_mb': round(self.max_cache_size / 1024 / 1024, 2)
            }
        except:
            return {'error': 'Unable to get cache stats'}


def test_image_manager():
    """Test the image manager"""
    print("=== Image Manager Test ===")
    
    manager = ImageManager()
    
    # Test URL
    test_url = "https://go-upc.s3.amazonaws.com/images/198534139.png"
    
    def download_callback(path, success):
        if success and path:
            print(f"✅ Download successful: {path}")
            
            # Test loading for display
            if PIL_AVAILABLE:
                photo = manager.load_image_for_display(path, (300, 300))
                if photo:
                    print(f"✅ Image loaded for display: {photo.width()}x{photo.height()}")
                else:
                    print("❌ Failed to load image for display")
        else:
            print("❌ Download failed")
    
    # Start download
    manager.download_image(test_url, download_callback)
    
    # Wait a bit for download
    time.sleep(3)
    
    # Show cache stats
    stats = manager.get_cache_stats()
    print(f"Cache stats: {stats}")

if __name__ == "__main__":
    test_image_manager()
