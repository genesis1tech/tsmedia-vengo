#!/usr/bin/env python3
"""
Animated Sleep Display using Pygame
Provides a screensaver-style animated sleep screen.
"""

import os
import sys
import time
import random
import math
import pygame
import logging
from typing import List, Tuple

class Star:
    """Represents a single star in the background"""
    def __init__(self, width: int, height: int):
        self.x = random.randint(0, width)
        self.y = random.randint(0, height)
        self.size = random.randint(1, 3)
        self.brightness = random.randint(50, 255)
        self.speed = random.uniform(0.05, 0.2)
        self.twinkle_speed = random.uniform(2, 5)
        self.twinkle_offset = random.uniform(0, 6.28)
        self.width = width
        self.height = height

    def update(self):
        """Update star position and brightness"""
        # Slow drift
        self.x -= self.speed
        if self.x < 0:
            self.x = self.width
            self.y = random.randint(0, self.height)

        # Twinkle effect
        time_val = time.time() * self.twinkle_speed + self.twinkle_offset
        self.current_brightness = int(self.brightness * (0.7 + 0.3 * math.sin(time_val)))

    def draw(self, surface: pygame.Surface):
        """Draw the star"""
        color = (self.current_brightness, self.current_brightness, self.current_brightness)
        if self.size == 1:
            surface.set_at((int(self.x), int(self.y)), color)
        else:
            pygame.draw.circle(surface, color, (int(self.x), int(self.y)), 1)

class AnimatedSleepDisplay:
    """Pygame-based animated sleep screen"""

    def __init__(self, width: int = 800, height: int = 480, fullscreen: bool = True):
        self.width = width
        self.height = height
        self.fullscreen = fullscreen
        self.running = False
        self.logger = logging.getLogger(__name__)
        
        # Initialize Pygame
        os.environ['SDL_VIDEO_CENTERED'] = '1'
        # Hide mouse cursor
        pygame.mouse.set_visible(False)
        
        pygame.init()
        
        flags = pygame.FULLSCREEN if fullscreen else 0
        self.screen = pygame.display.set_mode((width, height), flags)
        pygame.display.set_caption("TSV6 Sleep Mode")
        
        # Fonts
        try:
            self.font_large = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
            self.font_small = pygame.font.Font("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 32)
        except Exception:
            self.font_large = pygame.font.SysFont("Arial", 48, bold=True)
            self.font_small = pygame.font.SysFont("Arial", 32)

        # Stars
        self.stars: List[Star] = [Star(width, height) for _ in range(100)]
        
        # Text floating parameters
        self.text_x = width // 2
        self.text_y = height // 2
        self.text_dx = 1.0
        self.text_dy = 1.0
        
        # Colors
        self.bg_top = (10, 15, 30)      # Dark Blue
        self.bg_bottom = (5, 5, 15)     # Near Black
        
        # Pre-render gradient background to improve performance
        self.background = self._create_gradient_background()

    def _create_gradient_background(self) -> pygame.Surface:
        """Create a vertical gradient surface"""
        surface = pygame.Surface((self.width, self.height))
        for y in range(self.height):
            ratio = y / self.height
            r = int(self.bg_top[0] * (1 - ratio) + self.bg_bottom[0] * ratio)
            g = int(self.bg_top[1] * (1 - ratio) + self.bg_bottom[1] * ratio)
            b = int(self.bg_top[2] * (1 - ratio) + self.bg_bottom[2] * ratio)
            pygame.draw.line(surface, (r, g, b), (0, y), (self.width, y))
        return surface

    def _draw_moon(self, surface: pygame.Surface, x: int, y: int, radius: int):
        """Draw a glowing crescent moon"""
        # Glow
        for i in range(10, 0, -1):
            alpha = 100 - i * 10
            glow_surf = pygame.Surface((radius * 4, radius * 4), pygame.SRCALPHA)
            pygame.draw.circle(glow_surf, (74, 111, 165, 5), (radius * 2, radius * 2), radius + i * 3)
            surface.blit(glow_surf, (x - radius * 2, y - radius * 2))
            
        # Moon body
        pygame.draw.circle(surface, (230, 230, 240), (x, y), radius)
        
        # Crescent cutout (draw a circle of background color offset)
        # Since background is gradient, this is tricky. simpler: draw dark circle
        # But simpler approach: Pygame doesn't do subtraction easily without mask.
        # We will just draw a dark circle over it that matches the average background color roughly
        # Or better: Use a surface with colorkey for the moon shape.
        
        # Simplified approach for robustness:
        offset_x = int(radius * 0.5)
        offset_y = int(-radius * 0.2)
        bg_color = self.bg_top # Approximation
        pygame.draw.circle(surface, (15, 20, 40), (x + offset_x, y + offset_y), int(radius * 0.9))

    def _update_floating_text(self):
        """Update text position to float around"""
        self.text_x += self.text_dx
        self.text_y += self.text_dy
        
        # Bounce off bounds (keep text roughly in middle 60% of screen)
        margin_x = self.width * 0.2
        margin_y = self.height * 0.3
        
        if self.text_x < margin_x or self.text_x > self.width - margin_x:
            self.text_dx *= -1
        if self.text_y < margin_y or self.text_y > self.height - margin_y:
            self.text_dy *= -1

    def run(self, wake_time_str: str, stop_event=None):
        """
        Main loop for the animation.
        :param wake_time_str: String to display (e.g., "Waking at 7:00 AM")
        :param stop_event: Optional threading.Event to signal when to stop
        """
        self.running = True
        clock = pygame.time.Clock()
        
        try:
            while self.running:
                # Event processing
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self.running = False

                if stop_event and stop_event.is_set():
                    self.running = False

                # Update
                for star in self.stars:
                    star.update()
                self._update_floating_text()

                # Draw
                self.screen.blit(self.background, (0, 0))
                
                # Draw Stars
                for star in self.stars:
                    star.draw(self.screen)
                
                # Draw Moon (Static position top right)
                self._draw_moon(self.screen, self.width - 100, 80, 40)

                # Draw Text
                # Title
                title_surf = self.font_large.render("Sleeping...", True, (255, 255, 255))
                title_rect = title_surf.get_rect(center=(self.text_x, self.text_y))
                self.screen.blit(title_surf, title_rect)
                
                # Wake Time
                time_surf = self.font_small.render(f"Waking at {wake_time_str}", True, (180, 180, 200))
                time_rect = time_surf.get_rect(center=(self.text_x, self.text_y + 50))
                self.screen.blit(time_surf, time_rect)

                pygame.display.flip()
                
                # Cap framerate to 30 to save CPU
                clock.tick(30)
                
        except Exception as e:
            self.logger.error(f"Error in animation loop: {e}")
        finally:
            self.close()

    def close(self):
        """Cleanup pygame"""
        if self.running:
            self.running = False
            try:
                pygame.quit()
            except Exception:
                pass

if __name__ == "__main__":
    # Test run
    display = AnimatedSleepDisplay(fullscreen=False)
    display.run("8:00 AM")
