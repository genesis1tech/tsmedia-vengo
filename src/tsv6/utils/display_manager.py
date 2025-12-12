import vlc
import os
import time
import threading
import tempfile
from pathlib import Path
import sys
from PIL import Image, ImageDraw, ImageFont, ImageFilter

class DisplayManager:
    def __init__(self, width=800, height=480, fullscreen=True, image_directory="images"):
        """Initialize VLC-based display manager"""
        self.running = True
        self.width = width
        self.height = height
        self.fullscreen = fullscreen
        self.image_directory = Path(image_directory)
        self.current_state = "ready"
        self.slideshow_running = False
        self.slideshow_thread = None
        self.current_images = []
        self.image_duration = 5.0  # Duration to show each image in seconds

        # Initialize VLC instance with specific options for image display
        vlc_args = [
            '--intf', 'dummy',  # No interface
            '--no-video-title-show',  # Don't show video title
            '--image-duration', '5',  # Show each image for 5 seconds
        ]
        
        self.instance = vlc.Instance(vlc_args)
        self.player = self.instance.media_player_new()

        # Set fullscreen if requested
        if fullscreen:
            self.player.set_fullscreen(True)

        print(f"VLC Display Manager initialized - {width}x{height}")

    def set_state(self, state):
        """Set display state and update screen accordingly"""
        self.current_state = state
        self.stop_slideshow()

        if state == "ready":
            self.show_device_ready()
        elif state == "verify":
            self.start_slideshow(["verify1.jpg", "verify2.jpg"])
        elif state == "barcode_not_qr":
            self.show_message("Please scan barcode not QR code")
        elif state == "cannot_accept":
            self.show_message("Cannot accept this item")

    def start_slideshow(self, image_list):
        """Start slideshow with given image list using threading"""
        self.stop_slideshow()
        
        # Filter existing images
        valid_images = []
        for image in image_list:
            image_path = self.image_directory / image
            if image_path.exists():
                valid_images.append(str(image_path))
                print(f"Added to slideshow: {image_path}")
            else:
                print(f"Image not found: {image_path}")

        if not valid_images:
            print("No valid images found for slideshow")
            return

        self.current_images = valid_images
        self.slideshow_running = True
        
        # Start slideshow in separate thread
        self.slideshow_thread = threading.Thread(target=self._slideshow_loop, daemon=True)
        self.slideshow_thread.start()
        print(f"Started slideshow with {len(valid_images)} images")

    def _slideshow_loop(self):
        """Internal slideshow loop that cycles through images"""
        image_index = 0
        
        while self.slideshow_running and self.current_images:
            try:
                # Get current image
                current_image = self.current_images[image_index]
                
                # Create and play media
                media = self.instance.media_new(current_image)
                self.player.set_media(media)
                self.player.play()
                
                print(f"Displaying: {Path(current_image).name}")
                
                # Wait for the media to start playing
                time.sleep(0.5)
                
                # Wait for image duration
                time.sleep(self.image_duration)
                
                # Move to next image
                image_index = (image_index + 1) % len(self.current_images)
                
            except Exception as e:
                print(f"Error in slideshow loop: {e}")
                time.sleep(1)  # Brief pause before retrying

    def stop_slideshow(self):
        """Stop any running slideshow"""
        if self.slideshow_running:
            print("Stopping slideshow...")
            self.slideshow_running = False
            
            # Wait for slideshow thread to finish
            if self.slideshow_thread and self.slideshow_thread.is_alive():
                self.slideshow_thread.join(timeout=2.0)
            
            self.slideshow_thread = None
            self.current_images = []

    def display_single_image(self, image_path):
        """Display a single image"""
        self.stop_slideshow()  # Stop any running slideshow first
        
        if os.path.exists(image_path):
            try:
                media = self.instance.media_new(str(image_path))
                self.player.set_media(media)
                self.player.play()
                print(f"Displaying single image: {Path(image_path).name}")
            except Exception as e:
                print(f"Error displaying image {image_path}: {e}")
        else:
            print(f"Image not found: {image_path}")

    def show_device_ready(self):
        """Show ready screen with logo"""
        pepsi_image_path = self.image_directory / "pepsi.jpg"
        if pepsi_image_path.exists():
            self.display_single_image(str(pepsi_image_path))
        else:
            print("Ready screen image (pepsi.jpg) not found")

    def show_message(self, text, background_color=(0, 0, 0), text_color=(255, 255, 255)):
        """Show a message on screen by generating an image with text"""
        self.stop_slideshow()

        try:
            # Create image with solid black background
            img = Image.new('RGB', (self.width, self.height), background_color)
            draw = ImageDraw.Draw(img)

            # Start with a reasonable font size and adjust if needed
            max_width = self.width - 40  # Leave 20px margin on each side
            font_size = 48

            # Try to use a nice font, fall back to default
            font = None
            for font_path in [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
            ]:
                try:
                    font = ImageFont.truetype(font_path, font_size)
                    break
                except (IOError, OSError):
                    continue

            if font is None:
                font = ImageFont.load_default()

            # Reduce font size if text doesn't fit
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]

            while text_width > max_width and font_size > 20:
                font_size -= 4
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
                except (IOError, OSError):
                    font = ImageFont.load_default()
                bbox = draw.textbbox((0, 0), text, font=font)
                text_width = bbox[2] - bbox[0]

            # Calculate text position (centered)
            bbox = draw.textbbox((0, 0), text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            x = (self.width - text_width) // 2
            y = (self.height - text_height) // 2

            # Draw text
            draw.text((x, y), text, font=font, fill=text_color)

            # Save to temp file and display
            temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img.save(temp_file.name)
            temp_file.close()

            # Display the generated image
            media = self.instance.media_new(temp_file.name)
            self.player.set_media(media)
            self.player.play()

            print(f"Message displayed: {text}")

            # Store temp file path for cleanup
            self._temp_message_file = temp_file.name

        except Exception as e:
            print(f"Error showing message: {e}")
            self.clear_screen()

    def show_sleep_screen(self, wake_time: str):
        """Display animated sleep screen slideshow with multiple frames"""
        self.stop_slideshow()

        try:
            # Generate multiple sleep screen frames
            temp_files = []

            # Frame 1: Moon with stars
            img1 = self._create_sleep_frame_moon(wake_time)
            temp1 = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img1.save(temp1.name)
            temp1.close()
            temp_files.append(temp1.name)

            # Frame 2: Stars pattern
            img2 = self._create_sleep_frame_stars(wake_time)
            temp2 = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img2.save(temp2.name)
            temp2.close()
            temp_files.append(temp2.name)

            # Frame 3: Peaceful clouds/waves
            img3 = self._create_sleep_frame_clouds(wake_time)
            temp3 = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
            img3.save(temp3.name)
            temp3.close()
            temp_files.append(temp3.name)

            # Store temp files for cleanup
            self._temp_sleep_files = temp_files

            # Start slideshow with generated images
            self.current_images = temp_files
            self.slideshow_running = True
            self.image_duration = 8.0  # 8 seconds per frame

            # Start slideshow in separate thread
            self.slideshow_thread = threading.Thread(target=self._slideshow_loop, daemon=True)
            self.slideshow_thread.start()

            print(f"Sleep slideshow started: Waking at {wake_time}")

        except Exception as e:
            print(f"Error showing sleep screen: {e}")
            # Fallback to simple message
            self.show_message(f"Sleeping. Waking at {wake_time}")

    def _create_sleep_frame_moon(self, wake_time: str) -> Image.Image:
        """Create sleep frame with crescent moon"""
        img = self._create_gradient_background(
            top_color=(26, 42, 74),      # Dark navy blue
            bottom_color=(0, 0, 0)        # Black
        )

        # Draw moon with glow
        self._draw_moon_with_glow(img, self.width // 2, 150, 60)

        # Add some stars
        self._draw_stars(img, count=20, seed=42)

        # Add text
        self._draw_sleep_text(img, wake_time, "Sleeping")

        return img

    def _create_sleep_frame_stars(self, wake_time: str) -> Image.Image:
        """Create sleep frame with starry sky"""
        img = self._create_gradient_background(
            top_color=(15, 25, 55),       # Darker blue
            bottom_color=(5, 5, 20)        # Near black
        )

        # Draw lots of stars
        self._draw_stars(img, count=50, seed=123)

        # Draw a subtle constellation pattern
        self._draw_constellation(img)

        # Add text
        self._draw_sleep_text(img, wake_time, "Sweet Dreams")

        return img

    def _create_sleep_frame_clouds(self, wake_time: str) -> Image.Image:
        """Create sleep frame with peaceful clouds"""
        img = self._create_gradient_background(
            top_color=(40, 50, 80),       # Purple-blue
            bottom_color=(20, 25, 45)      # Dark purple
        )

        # Draw soft cloud shapes
        self._draw_clouds(img)

        # Add small moon in corner
        self._draw_moon_with_glow(img, 680, 80, 35)

        # Add text
        self._draw_sleep_text(img, wake_time, "Resting")

        return img

    def _draw_sleep_text(self, img: Image.Image, wake_time: str, main_text: str):
        """Draw sleep text on image"""
        draw = ImageDraw.Draw(img)

        try:
            font_large = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
        except (IOError, OSError):
            font_large = ImageFont.load_default()
            font_small = ImageFont.load_default()

        # Main text
        bbox = draw.textbbox((0, 0), main_text, font=font_large)
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        draw.text((x, 300), main_text, font=font_large, fill=(255, 255, 255))

        # Wake time text
        wake_text = f"Waking at {wake_time}"
        bbox = draw.textbbox((0, 0), wake_text, font=font_small)
        text_width = bbox[2] - bbox[0]
        x = (self.width - text_width) // 2
        draw.text((x, 370), wake_text, font=font_small, fill=(160, 160, 160))

    def _draw_stars(self, img: Image.Image, count: int = 30, seed: int = 42):
        """Draw random stars on image"""
        import random
        random.seed(seed)
        draw = ImageDraw.Draw(img)

        for _ in range(count):
            x = random.randint(20, self.width - 20)
            y = random.randint(20, 250)  # Upper portion only
            size = random.choice([1, 1, 1, 2, 2, 3])  # Mostly small stars
            brightness = random.randint(150, 255)

            if size == 1:
                draw.point((x, y), fill=(brightness, brightness, brightness))
            else:
                draw.ellipse([x-size, y-size, x+size, y+size],
                           fill=(brightness, brightness, brightness))

    def _draw_constellation(self, img: Image.Image):
        """Draw a simple constellation pattern"""
        draw = ImageDraw.Draw(img)

        # Simple constellation points (like Orion's belt)
        points = [(300, 100), (350, 110), (400, 105), (380, 160), (320, 155)]

        # Draw stars at constellation points
        for x, y in points:
            draw.ellipse([x-3, y-3, x+3, y+3], fill=(220, 220, 255))

        # Draw faint lines connecting them
        for i in range(len(points) - 1):
            draw.line([points[i], points[i+1]], fill=(80, 80, 120), width=1)

    def _draw_clouds(self, img: Image.Image):
        """Draw soft cloud shapes"""
        draw = ImageDraw.Draw(img)

        # Cloud positions and sizes
        clouds = [
            (100, 120, 80),   # x, y, size
            (300, 80, 100),
            (550, 140, 70),
            (700, 100, 60),
        ]

        for cx, cy, size in clouds:
            # Draw overlapping ellipses for cloud effect
            cloud_color = (60, 65, 90)  # Subtle gray-purple
            for offset in [(0, 0), (-size//3, 5), (size//3, 5), (0, size//4)]:
                x, y = cx + offset[0], cy + offset[1]
                r = size // 2
                draw.ellipse([x-r, y-r//2, x+r, y+r//2], fill=cloud_color)

    def _create_gradient_background(self, top_color: tuple, bottom_color: tuple) -> Image.Image:
        """Create vertical gradient background image"""
        img = Image.new('RGB', (self.width, self.height))

        for y in range(self.height):
            # Linear interpolation between colors
            ratio = y / self.height
            r = int(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
            g = int(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
            b = int(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)

            for x in range(self.width):
                img.putpixel((x, y), (r, g, b))

        return img

    def _draw_moon_with_glow(self, img: Image.Image, center_x: int, center_y: int, radius: int):
        """Draw crescent moon with glow effect"""
        # Create a separate layer for the glow
        glow_layer = Image.new('RGBA', (self.width, self.height), (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow_layer)

        # Draw multiple circles for glow effect (outer to inner)
        glow_color = (74, 111, 165)  # #4a6fa5 blue glow
        for i in range(5, 0, -1):
            glow_radius = radius + i * 8
            alpha = int(40 - i * 6)  # Decreasing alpha for outer rings
            glow_draw.ellipse(
                [center_x - glow_radius, center_y - glow_radius,
                 center_x + glow_radius, center_y + glow_radius],
                fill=(glow_color[0], glow_color[1], glow_color[2], alpha)
            )

        # Apply blur to glow layer
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=10))

        # Composite glow onto main image
        img.paste(Image.alpha_composite(img.convert('RGBA'), glow_layer).convert('RGB'), (0, 0))

        # Draw the crescent moon
        moon_color = (232, 232, 240)  # #e8e8f0 soft white

        # Main moon circle
        draw = ImageDraw.Draw(img)
        draw.ellipse(
            [center_x - radius, center_y - radius,
             center_x + radius, center_y + radius],
            fill=moon_color
        )

        # Cut out part to create crescent (offset circle in background color)
        # Offset to the upper-right to create crescent effect
        offset_x = radius * 0.6
        offset_y = -radius * 0.3
        cutout_radius = radius * 0.85

        # Get the background color at the cutout position (use gradient color)
        bg_ratio = center_y / self.height
        bg_r = int(26 * (1 - bg_ratio) + 0 * bg_ratio)
        bg_g = int(42 * (1 - bg_ratio) + 0 * bg_ratio)
        bg_b = int(74 * (1 - bg_ratio) + 0 * bg_ratio)

        draw.ellipse(
            [center_x + offset_x - cutout_radius, center_y + offset_y - cutout_radius,
             center_x + offset_x + cutout_radius, center_y + offset_y + cutout_radius],
            fill=(bg_r, bg_g, bg_b)
        )

    def clear_screen(self, background_color=None):
        """Clear screen - background_color parameter added for compatibility"""
        self.stop_slideshow()
        self.player.stop()

    def draw_text(self, text, x, y, font_size=16, color=(255, 255, 255), background_color=None):
        """Draw text on screen - stub implementation for VLC compatibility"""
        # Since you don't need text drawing, this is just a stub
        pass

    def draw_text_centered(self, text, center_x, center_y, font_size=16, color=(255, 255, 255)):
        """Draw centered text - stub implementation for VLC compatibility"""
        # Since you don't need text drawing, this is just a stub
        pass

    def get_width(self):
        """Get display width"""
        return self.width

    def get_height(self):
        """Get display height"""
        return self.height

    def set_image_duration(self, duration):
        """Set duration for each image in slideshow (seconds)"""
        self.image_duration = max(1.0, float(duration))
        print(f"Image duration set to {self.image_duration} seconds")

    def is_slideshow_running(self):
        """Check if slideshow is currently running"""
        return self.slideshow_running

    def get_current_images(self):
        """Get list of current slideshow images"""
        return self.current_images.copy()

    def close(self):
        """Clean up resources"""
        print("Closing VLC Display Manager...")
        self.running = False
        self.stop_slideshow()
        
        try:
            self.player.stop()
            self.player.release()
            self.instance.release()
            print("VLC resources cleaned up")
        except Exception as e:
            print(f"Error during VLC cleanup: {e}")

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()