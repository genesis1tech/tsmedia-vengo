"""
Modern UI Theme for TSV6 Product Display
=========================================

Apple-inspired design system using Inter font from Google Fonts.
Clean, minimal, and highly readable for kiosk displays.
"""

from dataclasses import dataclass
from pathlib import Path
from PIL import ImageFont
import os


@dataclass
class ColorPalette:
    """Apple-inspired color palette"""
    # Backgrounds
    background: str = "#F5F5F7"          # Soft light gray (Apple signature)
    background_alt: str = "#FFFFFF"       # Pure white for cards

    # Text colors
    text_primary: str = "#1D1D1F"         # Soft black for headlines
    text_secondary: str = "#86868B"       # Muted gray for secondary text
    text_tertiary: str = "#AEAEB2"        # Light gray for subtle text

    # Accent colors
    accent_blue: str = "#0071E3"          # Apple blue
    accent_green: str = "#34C759"         # Success green
    accent_orange: str = "#FF9500"        # Warning orange

    # QR code colors
    qr_foreground: str = "#1D1D1F"        # Soft black for QR modules
    qr_background: str = "#FFFFFF"        # White for QR background

    # Countdown colors
    countdown_text: str = "#86868B"       # Muted for countdown
    countdown_circle: str = "#E5E5EA"     # Light gray circle background
    countdown_progress: str = "#0071E3"   # Blue progress arc


@dataclass
class Typography:
    """Typography settings with Inter font"""
    # Font paths
    font_dir: Path = Path(__file__).parent.parent.parent.parent / "assets" / "fonts" / "extras" / "ttf"

    # Font files
    font_light: str = "Inter-Light.ttf"
    font_regular: str = "Inter-Regular.ttf"
    font_medium: str = "Inter-Medium.ttf"
    font_semibold: str = "Inter-SemiBold.ttf"
    font_bold: str = "Inter-Bold.ttf"

    # Display variants (optimized for larger sizes)
    font_display_light: str = "InterDisplay-Light.ttf"
    font_display_regular: str = "InterDisplay-Regular.ttf"
    font_display_medium: str = "InterDisplay-Medium.ttf"
    font_display_semibold: str = "InterDisplay-SemiBold.ttf"
    font_display_bold: str = "InterDisplay-Bold.ttf"

    # Font sizes for 800x480 display (scaled for readability)
    size_headline: int = 28              # Product name
    size_subheadline: int = 20           # Brand name
    size_body: int = 18                  # Body text
    size_caption: int = 14               # Small text
    size_cta: int = 22                   # Call to action ("Scan for rewards")
    size_countdown: int = 36             # Countdown timer
    size_countdown_label: int = 12       # "seconds" label

    def get_font_path(self, font_file: str) -> str:
        """Get full path to font file"""
        return str(self.font_dir / font_file)

    def load_font(self, weight: str = "regular", size: int = 18, display: bool = False) -> ImageFont.FreeTypeFont:
        """
        Load Inter font with specified weight and size.

        Args:
            weight: Font weight (light, regular, medium, semibold, bold)
            size: Font size in pixels
            display: Use display variant (optimized for large sizes)

        Returns:
            PIL ImageFont object
        """
        weight_map = {
            "light": self.font_display_light if display else self.font_light,
            "regular": self.font_display_regular if display else self.font_regular,
            "medium": self.font_display_medium if display else self.font_medium,
            "semibold": self.font_display_semibold if display else self.font_semibold,
            "bold": self.font_display_bold if display else self.font_bold,
        }

        font_file = weight_map.get(weight, self.font_regular)
        font_path = self.get_font_path(font_file)

        try:
            return ImageFont.truetype(font_path, size)
        except Exception as e:
            print(f"Warning: Could not load Inter font: {e}, falling back to default")
            return ImageFont.load_default()


@dataclass
class Spacing:
    """Spacing system (8px base grid)"""
    xs: int = 4      # Extra small
    sm: int = 8      # Small
    md: int = 16     # Medium
    lg: int = 24     # Large
    xl: int = 32     # Extra large
    xxl: int = 48    # 2x Extra large

    # Specific spacing
    card_padding: int = 24
    section_gap: int = 40
    element_gap: int = 12


@dataclass
class Dimensions:
    """Component dimensions for 800x480 display"""
    # Screen
    screen_width: int = 800
    screen_height: int = 480

    # Product image (left side)
    product_image_max_width: int = 320
    product_image_max_height: int = 320

    # QR code (right side)
    qr_size: int = 200                   # QR code size
    qr_container_padding: int = 20       # Padding around QR in card
    qr_corner_radius: int = 16           # Rounded corners for QR card

    # Countdown
    countdown_size: int = 60             # Circular countdown diameter
    countdown_stroke: int = 4            # Circle stroke width


class ModernTheme:
    """
    Complete modern theme for TSV6 product display.

    Usage:
        from tsv6.ui.modern_theme import theme

        # Access colors
        bg_color = theme.colors.background

        # Load fonts
        headline_font = theme.typography.load_font("semibold", theme.typography.size_headline)

        # Access spacing
        padding = theme.spacing.card_padding
    """

    def __init__(self):
        self.colors = ColorPalette()
        self.typography = Typography()
        self.spacing = Spacing()
        self.dimensions = Dimensions()

    def hex_to_rgb(self, hex_color: str) -> tuple:
        """Convert hex color to RGB tuple"""
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def hex_to_tk(self, hex_color: str) -> str:
        """Ensure hex color is in tkinter format"""
        if not hex_color.startswith('#'):
            return f"#{hex_color}"
        return hex_color


# Global theme instance
theme = ModernTheme()


# Convenience exports
__all__ = [
    'theme',
    'ModernTheme',
    'ColorPalette',
    'Typography',
    'Spacing',
    'Dimensions'
]
