#!/usr/bin/env python3
"""
Digital Photo Frame Application
A simple digital photo frame for Raspberry Pi Zero 2 W
"""

import os
import sys
import time
import random
import logging
from pathlib import Path
from typing import List, Optional
import yaml
import pygame
from PIL import Image


class PhotoFrame:
    """Main photo frame class handling display and slideshow logic."""
    
    def __init__(self, config_path: str = "config.yaml"):
        """Initialize the photo frame with configuration."""
        self.config = self._load_config(config_path)
        self.setup_logging()
        self.logger = logging.getLogger(__name__)
        
        # Initialize pygame
        pygame.init()
        
        # Get display settings
        self.fullscreen = self.config.get('display', {}).get('fullscreen', True)
        self.width = self.config.get('display', {}).get('width', 800)
        self.height = self.config.get('display', {}).get('height', 600)
        
        # Setup display
        if self.fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            self.width, self.height = self.screen.get_size()
        else:
            self.screen = pygame.display.set_mode((self.width, self.height))
        
        pygame.display.set_caption("Digital Photo Frame")
        pygame.mouse.set_visible(False)
        
        # Get slideshow settings
        self.interval = self.config.get('slideshow', {}).get('interval', 10)
        self.shuffle = self.config.get('slideshow', {}).get('shuffle', True)
        self.transition = self.config.get('slideshow', {}).get('transition', 'fade')
        self.fade_duration = self.config.get('slideshow', {}).get('fade_duration', 1.0)
        
        # Get image settings
        self.image_dir = Path(self.config.get('images', {}).get('directory', './images'))
        self.recursive = self.config.get('images', {}).get('recursive', True)
        self.extensions = self.config.get('images', {}).get('extensions', 
                                                           ['.jpg', '.jpeg', '.png', '.bmp', '.gif'])
        
        # Load images
        self.images = self._load_image_list()
        if not self.images:
            self.logger.error(f"No images found in {self.image_dir}")
            sys.exit(1)
        
        self.logger.info(f"Loaded {len(self.images)} images from {self.image_dir}")
        
        if self.shuffle:
            random.shuffle(self.images)
        
        self.current_index = 0
        self.running = True
        self.clock = pygame.time.Clock()
    
    def _load_config(self, config_path: str) -> dict:
        """Load configuration from YAML file."""
        if os.path.exists(config_path):
            with open(config_path, 'r') as f:
                return yaml.safe_load(f) or {}
        return {}
    
    def setup_logging(self):
        """Setup logging configuration."""
        log_config = self.config.get('logging', {})
        log_level = getattr(logging, log_config.get('level', 'INFO').upper())
        log_file = log_config.get('file', 'photo_frame.log')
        
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler()
            ]
        )
    
    def _load_image_list(self) -> List[Path]:
        """Load list of image files from directory."""
        images = []
        
        if not self.image_dir.exists():
            self.logger.warning(f"Image directory {self.image_dir} does not exist, creating it...")
            self.image_dir.mkdir(parents=True, exist_ok=True)
            return images
        
        if self.recursive:
            for ext in self.extensions:
                images.extend(self.image_dir.rglob(f"*{ext}"))
                images.extend(self.image_dir.rglob(f"*{ext.upper()}"))
        else:
            for ext in self.extensions:
                images.extend(self.image_dir.glob(f"*{ext}"))
                images.extend(self.image_dir.glob(f"*{ext.upper()}"))
        
        return sorted(images)
    
    def _load_and_scale_image(self, image_path: Path) -> Optional[pygame.Surface]:
        """Load and scale image to fit screen while maintaining aspect ratio."""
        try:
            # Load image with PIL
            img = Image.open(image_path)
            
            # Convert to RGB if necessary
            if img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Get image dimensions
            img_width, img_height = img.size
            
            # Calculate scaling to fit screen while maintaining aspect ratio
            scale_w = self.width / img_width
            scale_h = self.height / img_height
            scale = min(scale_w, scale_h)
            
            new_width = int(img_width * scale)
            new_height = int(img_height * scale)
            
            # Resize image
            img = img.resize((new_width, new_height), Image.LANCZOS)
            
            # Convert PIL image to pygame surface
            img_str = img.tobytes()
            pygame_image = pygame.image.fromstring(img_str, img.size, 'RGB')
            
            return pygame_image
            
        except Exception as e:
            self.logger.error(f"Error loading image {image_path}: {e}")
            return None
    
    def _draw_image(self, surface: pygame.Surface, alpha: int = 255):
        """Draw image centered on screen with optional alpha."""
        # Fill screen with black
        self.screen.fill((0, 0, 0))
        
        # Calculate centered position
        img_rect = surface.get_rect()
        x = (self.width - img_rect.width) // 2
        y = (self.height - img_rect.height) // 2
        
        # Apply alpha if needed
        if alpha < 255:
            surface = surface.copy()
            surface.set_alpha(alpha)
        
        # Blit image to screen
        self.screen.blit(surface, (x, y))
        pygame.display.flip()
    
    def _fade_transition(self, old_surface: Optional[pygame.Surface], 
                        new_surface: pygame.Surface):
        """Perform fade transition between images."""
        if old_surface is None:
            self._draw_image(new_surface)
            return
        
        # Calculate fade steps
        fps = 30
        steps = int(self.fade_duration * fps)
        
        for i in range(steps + 1):
            alpha = int((i / steps) * 255)
            
            # Fill screen with black
            self.screen.fill((0, 0, 0))
            
            # Draw old image
            old_rect = old_surface.get_rect()
            old_x = (self.width - old_rect.width) // 2
            old_y = (self.height - old_rect.height) // 2
            self.screen.blit(old_surface, (old_x, old_y))
            
            # Draw new image with alpha (create fresh copy each iteration)
            new_surf_alpha = new_surface.copy()
            new_surf_alpha.set_alpha(alpha)
            new_rect = new_surf_alpha.get_rect()
            new_x = (self.width - new_rect.width) // 2
            new_y = (self.height - new_rect.height) // 2
            self.screen.blit(new_surf_alpha, (new_x, new_y))
            
            pygame.display.flip()
            
            # Check for quit events during transition
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                    return
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                        self.running = False
                        return
            
            self.clock.tick(fps)
    
    def display_next_image(self, previous_surface: Optional[pygame.Surface] = None):
        """Display the next image in the slideshow."""
        if not self.images:
            return None
        
        # Get next image
        image_path = self.images[self.current_index]
        self.logger.info(f"Displaying: {image_path.name}")
        
        # Load and scale image
        surface = self._load_and_scale_image(image_path)
        
        if surface is None:
            # Skip to next image if loading failed
            self.current_index = (self.current_index + 1) % len(self.images)
            return self.display_next_image(previous_surface)
        
        # Apply transition
        if self.transition == 'fade' and previous_surface is not None:
            self._fade_transition(previous_surface, surface)
        else:
            self._draw_image(surface)
        
        # Update index for next image
        self.current_index = (self.current_index + 1) % len(self.images)
        
        return surface
    
    def run(self):
        """Main loop for the photo frame."""
        self.logger.info("Starting photo frame...")
        
        current_surface = None
        last_update = time.time()
        
        while self.running:
            # Handle events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False
                elif event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE or event.key == pygame.K_q:
                        self.running = False
                    elif event.key == pygame.K_SPACE:
                        # Manual advance to next image
                        current_surface = self.display_next_image(current_surface)
                        last_update = time.time()
                    elif event.key == pygame.K_r:
                        # Reload image list
                        self.images = self._load_image_list()
                        if self.shuffle:
                            random.shuffle(self.images)
                        self.logger.info(f"Reloaded {len(self.images)} images")
            
            # Check if it's time to display next image
            current_time = time.time()
            if current_time - last_update >= self.interval:
                current_surface = self.display_next_image(current_surface)
                last_update = current_time
            
            # Limit frame rate
            self.clock.tick(10)
        
        self.cleanup()
    
    def cleanup(self):
        """Cleanup resources."""
        self.logger.info("Shutting down photo frame...")
        pygame.quit()


def main():
    """Main entry point."""
    # Get config path from command line or use default
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    
    try:
        frame = PhotoFrame(config_path)
        frame.run()
    except KeyboardInterrupt:
        print("\nShutdown requested... exiting")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
