#!/usr/bin/env python3
"""
Splash Screen Utility for TSV6

Displays a splash screen with an image and text overlay.
Used during LTE startup wait to inform user of connection status.
"""

import os
import logging
import tempfile
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Try to import display libraries
try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning("PIL not available - splash screen will be text-only")

try:
    import pygame
    PYGAME_AVAILABLE = True
except ImportError:
    PYGAME_AVAILABLE = False
    logger.warning("pygame not available - splash screen disabled")


class SplashScreen:
    """
    Displays a splash screen with an image and text overlay.

    Usage:
        splash = SplashScreen()
        splash.show("Please wait connecting to 4G LTE", image_path="/path/to/logo.jpg")
        # ... do work ...
        splash.hide()
    """

    # Default display dimensions (Waveshare 7" DSI display)
    DEFAULT_WIDTH = 800
    DEFAULT_HEIGHT = 480

    def __init__(
        self,
        width: int = DEFAULT_WIDTH,
        height: int = DEFAULT_HEIGHT,
        background_color: Tuple[int, int, int] = (0, 0, 0),
    ):
        """
        Initialize splash screen.

        Args:
            width: Display width in pixels
            height: Display height in pixels
            background_color: RGB tuple for background color
        """
        self.width = width
        self.height = height
        self.background_color = background_color
        self._screen = None
        self._is_showing = False
        self._temp_file: Optional[str] = None

    def _create_splash_image(
        self,
        text: str,
        image_path: Optional[str] = None,
        text_color: Tuple[int, int, int] = (255, 255, 255),
        text_position: str = "bottom",  # "top", "center", "bottom"
        font_size: int = 32,
    ) -> Optional[Image.Image]:
        """
        Create splash image with text overlay.

        Args:
            text: Text to display
            image_path: Path to background image (optional)
            text_color: RGB tuple for text color
            text_position: Position of text ("top", "center", "bottom")
            font_size: Font size for text

        Returns:
            PIL Image object or None if PIL not available
        """
        if not PIL_AVAILABLE:
            return None

        # Create base image
        if image_path and Path(image_path).exists():
            try:
                img = Image.open(image_path)
                # Resize to fill display while maintaining aspect ratio
                img = img.convert('RGB')

                # Calculate scale to fill screen
                scale_w = self.width / img.width
                scale_h = self.height / img.height
                scale = max(scale_w, scale_h)  # Use larger scale to fill screen

                new_width = int(img.width * scale)
                new_height = int(img.height * scale)
                img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                # Center crop to exact dimensions
                left = (new_width - self.width) // 2
                top = (new_height - self.height) // 2
                img = img.crop((left, top, left + self.width, top + self.height))

            except Exception as e:
                logger.warning(f"Failed to load image {image_path}: {e}")
                img = Image.new('RGB', (self.width, self.height), self.background_color)
        else:
            img = Image.new('RGB', (self.width, self.height), self.background_color)

        # Add text overlay
        draw = ImageDraw.Draw(img)

        # Try to load a nice font
        font = None
        font_paths = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]

        for font_path in font_paths:
            try:
                font = ImageFont.truetype(font_path, font_size)
                break
            except (IOError, OSError):
                continue

        if font is None:
            font = ImageFont.load_default()

        # Calculate text size and position
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Center horizontally
        x = (self.width - text_width) // 2

        # Vertical position based on setting
        if text_position == "top":
            y = 40
        elif text_position == "center":
            y = (self.height - text_height) // 2
        else:  # bottom
            y = self.height - text_height - 60

        # Draw text with shadow for better visibility
        shadow_offset = 2
        shadow_color = (0, 0, 0)

        # Draw shadow
        draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)

        # Draw main text
        draw.text((x, y), text, font=font, fill=text_color)

        return img

    def show(
        self,
        text: str,
        image_path: Optional[str] = None,
        text_color: Tuple[int, int, int] = (255, 255, 255),
        text_position: str = "bottom",
        font_size: int = 32,
    ) -> bool:
        """
        Show the splash screen.

        Args:
            text: Text to display on the splash screen
            image_path: Path to background image (optional)
            text_color: RGB tuple for text color
            text_position: Position of text ("top", "center", "bottom")
            font_size: Font size for text

        Returns:
            True if splash shown successfully
        """
        if not PYGAME_AVAILABLE:
            logger.warning("Cannot show splash: pygame not available")
            # Just log the message as fallback
            logger.info(f"SPLASH: {text}")
            return False

        try:
            # Initialize pygame if needed
            if not pygame.get_init():
                pygame.init()

            # Hide cursor
            pygame.mouse.set_visible(False)

            # Create display
            if self._screen is None:
                # Try to create fullscreen display
                try:
                    self._screen = pygame.display.set_mode(
                        (self.width, self.height),
                        pygame.FULLSCREEN | pygame.NOFRAME
                    )
                except pygame.error:
                    # Fallback to windowed mode
                    self._screen = pygame.display.set_mode((self.width, self.height))

                pygame.display.set_caption("TSV6 Splash")

            # Create splash image
            splash_img = self._create_splash_image(
                text=text,
                image_path=image_path,
                text_color=text_color,
                text_position=text_position,
                font_size=font_size,
            )

            if splash_img:
                # Save to temp file and load with pygame
                self._temp_file = tempfile.NamedTemporaryFile(
                    suffix='.png', delete=False
                ).name
                splash_img.save(self._temp_file)

                # Load and display
                pygame_img = pygame.image.load(self._temp_file)
                self._screen.blit(pygame_img, (0, 0))
            else:
                # Fallback: just display text on black background
                self._screen.fill(self.background_color)

                try:
                    pygame_font = pygame.font.SysFont('dejavusans', font_size)
                except:
                    pygame_font = pygame.font.Font(None, font_size)

                text_surface = pygame_font.render(text, True, text_color)
                text_rect = text_surface.get_rect(
                    center=(self.width // 2, self.height - 60)
                )
                self._screen.blit(text_surface, text_rect)

            pygame.display.flip()
            self._is_showing = True

            logger.info(f"Splash screen displayed: {text}")
            return True

        except Exception as e:
            logger.error(f"Failed to show splash screen: {e}")
            return False

    def update_text(
        self,
        text: str,
        image_path: Optional[str] = None,
        text_color: Tuple[int, int, int] = (255, 255, 255),
        text_position: str = "bottom",
        font_size: int = 32,
    ) -> bool:
        """
        Update the splash screen text without reinitializing.

        Args:
            text: New text to display
            image_path: Path to background image (optional)
            text_color: RGB tuple for text color
            text_position: Position of text
            font_size: Font size

        Returns:
            True if update successful
        """
        if not self._is_showing:
            return self.show(text, image_path, text_color, text_position, font_size)

        return self.show(text, image_path, text_color, text_position, font_size)

    def hide(self) -> None:
        """Hide the splash screen and clean up resources."""
        if self._screen is not None:
            try:
                pygame.display.quit()
            except Exception as e:
                logger.debug(f"Error closing pygame display: {e}")
            self._screen = None

        # Clean up temp file
        if self._temp_file and os.path.exists(self._temp_file):
            try:
                os.unlink(self._temp_file)
            except Exception as e:
                logger.debug(f"Error removing temp file: {e}")
            self._temp_file = None

        self._is_showing = False
        logger.info("Splash screen hidden")

    def is_showing(self) -> bool:
        """Check if splash screen is currently showing."""
        return self._is_showing

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensure cleanup."""
        self.hide()


# Convenience function for simple usage
def show_lte_connecting_splash(
    image_path: Optional[str] = None,
    text: str = "Please wait connecting to 4G LTE",
) -> SplashScreen:
    """
    Show the LTE connecting splash screen.

    Args:
        image_path: Path to background image (defaults to g1tech.jpg)
        text: Text to display

    Returns:
        SplashScreen instance (call .hide() when done)
    """
    # Default to g1tech.jpg
    if image_path is None:
        # Try common locations
        possible_paths = [
            "/home/g1tech/tsrpi5/event_images/g1tech.jpg",
            "event_images/g1tech.jpg",
            "/home/g1tech/event_images/g1tech.jpg",
        ]
        for path in possible_paths:
            if Path(path).exists():
                image_path = path
                break

    splash = SplashScreen()
    splash.show(text=text, image_path=image_path)
    return splash


if __name__ == "__main__":
    # Test the splash screen
    import time

    print("Testing splash screen...")

    # Test with g1tech logo
    splash = show_lte_connecting_splash()

    print("Splash displayed, waiting 5 seconds...")
    time.sleep(5)

    # Update text
    splash.update_text(
        "LTE connected!",
        image_path="/home/g1tech/tsrpi5/event_images/g1tech.jpg"
    )
    time.sleep(2)

    splash.hide()
    print("Splash hidden, test complete")
