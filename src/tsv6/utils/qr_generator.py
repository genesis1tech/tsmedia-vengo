import qrcode
from PIL import Image, ImageDraw
import os
from pathlib import Path
import time

class QRDisplayManager:
    def __init__(self, width=800, height=480, module_scale=5, quiet_zone_margin=5):
        """
        Initialize QR Display Manager - matches Arduino QRDisplayManager
        
        Args:
            width: Display width (equivalent to display.width() in Arduino)
            height: Display height (equivalent to display.height() in Arduino)
            module_scale: Size of each QR module in pixels (default 5 like Arduino)
            quiet_zone_margin: White border around QR code (default 5 like Arduino)
        """
        self.display_width = width
        self.display_height = height
        self.module_scale = module_scale
        self.quiet_zone_margin = quiet_zone_margin
        
        # Position will be calculated for each QR code
        self.x_offset = 0
        self.y_offset = 0
    
    def calculate_position(self, qr_size):
        """
        Calculate QR code position - exactly matches Arduino calculatePosition()
        
        Args:
            qr_size: Number of modules per side in the QR code
        """
        # Exact Arduino positioning logic
        self.x_offset = 33
        self.y_offset = 446 - (qr_size * self.module_scale)
    
    def draw_background(self, qr_size, background_path="/images/toronto_qr.jpg"):
        """
        Draw background image and white rectangle - matches Arduino drawBackground()
        
        Args:
            qr_size: Number of modules per side in the QR code
            background_path: Path to background image
            
        Returns:
            PIL Image with background and white QR area
        """
        try:
            # Load background image (equivalent to TJpgDec.drawSdJpg in Arduino)
            if os.path.exists(background_path):
                background = Image.open(background_path).convert("RGB")
                background = background.resize((self.display_width, self.display_height), Image.LANCZOS)
            else:
                # Fallback to black background
                background = Image.new('RGB', (self.display_width, self.display_height), color=(0, 0, 0))
            
            # Draw white rectangle for QR code background (equivalent to display.fillRect in Arduino)
            draw = ImageDraw.Draw(background)
            rect_width = (qr_size * self.module_scale) + (self.quiet_zone_margin * 2)
            rect_height = (qr_size * self.module_scale) + (self.quiet_zone_margin * 2)
            
            draw.rectangle([
                self.x_offset - self.quiet_zone_margin,
                self.y_offset - self.quiet_zone_margin,
                self.x_offset - self.quiet_zone_margin + rect_width,
                self.y_offset - self.quiet_zone_margin + rect_height
            ], fill="white")
            
            return background
            
        except Exception as e:
            print(f"Error drawing background: {e}")
            return Image.new('RGB', (self.display_width, self.display_height), color=(255, 255, 255))
    
    def draw_qr_code(self, qr_code_obj, background_img):
        """
        Draw QR code modules - exactly matches Arduino drawQRCode()
        
        Args:
            qr_code_obj: QRCode object with modules data
            background_img: Background image to draw on
            
        Returns:
            Image with QR code drawn
        """
        draw = ImageDraw.Draw(background_img)
        modules = qr_code_obj.modules
        
        # Draw each module (equivalent to nested for loops in Arduino)
        for y in range(len(modules)):
            for x in range(len(modules[y])):
                if modules[y][x]:  # If module is black (equivalent to qrcode_getModule check)
                    draw.rectangle([
                        self.x_offset + (x * self.module_scale),
                        self.y_offset + (y * self.module_scale),
                        self.x_offset + (x * self.module_scale) + self.module_scale,
                        self.y_offset + (y * self.module_scale) + self.module_scale
                    ], fill="black")
        
        return background_img
    
    def display_qr_code(self, text, version=3, ecc=0):
        """
        Main QR code display function - exactly matches Arduino displayQRCode()
        
        Args:
            text: Text to encode in QR code
            version: QR code version (default 3 like Arduino)
            ecc: Error correction level (0=L, 1=M, 2=Q, 3=H)
            
        Returns:
            Tuple: (generation_time_ms, PIL_Image)
        """
        start_time = time.time()
        
        # Map Arduino error correction levels to Python constants
        ecc_levels = [
            qrcode.constants.ERROR_CORRECT_L,  # 0
            qrcode.constants.ERROR_CORRECT_M,  # 1
            qrcode.constants.ERROR_CORRECT_Q,  # 2
            qrcode.constants.ERROR_CORRECT_H   # 3
        ]
        
        try:
            # Create QR code (equivalent to qrcode_initText in Arduino)
            qr = qrcode.QRCode(
                version=version,
                error_correction=ecc_levels[ecc] if ecc < len(ecc_levels) else qrcode.constants.ERROR_CORRECT_M,
                box_size=1,  # We handle scaling manually
                border=0,    # We handle margin manually
            )
            qr.add_data(text)
            qr.make(fit=True)
            
            # Get QR code size (equivalent to qrcode.size in Arduino)
            qr_size = len(qr.modules)
            
            # Step 1: Calculate position (matches Arduino calculatePosition)
            self.calculate_position(qr_size)
            
            # Step 2: Draw background (matches Arduino drawBackground)
            background_img = self.draw_background(qr_size)
            
            # Step 3: Draw QR code (matches Arduino drawQRCode)
            final_img = self.draw_qr_code(qr, background_img)
            
            # Calculate generation time in milliseconds (like Arduino millis())
            generation_time_ms = int((time.time() - start_time) * 1000)
            
            return generation_time_ms, final_img
            
        except Exception as e:
            print(f"Error generating QR code: {e}")
            return 0, None
    
    def display_qr_with_transaction(self, transaction_id):
        """
        Display QR code with transaction URL - exactly matches Arduino displayQRWithTransaction()
        
        Args:
            transaction_id: Transaction identifier
            
        Returns:
            Tuple: (generation_time_ms, PIL_Image)
        """
        base_url = "https://eco-rewards.netlify.app/"
        url = f"{base_url}?utm_id={transaction_id}"
        
        # Use QR code version 6 like Arduino (comment says 4 but code uses 6)
        return self.display_qr_code(url, version=6, ecc=0)
    
    # Getters and setters for customization (matches Arduino interface)
    def set_scale(self, new_scale):
        """Set module scale"""
        self.module_scale = new_scale
    
    def set_margin(self, new_margin):
        """Set quiet zone margin"""
        self.quiet_zone_margin = new_margin
    
    def get_scale(self):
        """Get current module scale"""
        return self.module_scale
    
    def get_margin(self):
        """Get current margin"""
        return self.quiet_zone_margin

# Convenience function for backwards compatibility
def generate_qr_code(data, size=200, path=None):
    """
    Simple function to generate a QR code (backwards compatibility)
    Creates a standard QR code without background image
    """
    try:
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img = img.resize((size, size), Image.LANCZOS)
        
        if path:
            img.save(path)
            print(f"QR code saved to: {path}")
        
        return img
    except Exception as e:
        print(f"Error generating QR code: {e}")
        return None

# Example usage
if __name__ == "__main__":
    # Create QR display manager (like Arduino constructor)
    qr_manager = QRDisplayManager(
        width=800, 
        height=480, 
        module_scale=5,        # moduleScale = 5 (Arduino default)
        quiet_zone_margin=5    # quietZoneMargin = 5 (Arduino default)
    )
    
    # Test basic QR code generation
    test_data = "https://genesis1.app"
    generation_time, qr_image = qr_manager.display_qr_code(test_data, version=3, ecc=1)
    
    if qr_image:
        print(f"QR code generated successfully in {generation_time}ms")
        qr_image.save("test_qr.png")
    
    # Test transaction QR (like Arduino displayQRWithTransaction)
    transaction_id = "TXN123456"
    gen_time, transaction_img = qr_manager.display_qr_with_transaction(transaction_id)
    
    if transaction_img:
        print(f"Transaction QR generated in {gen_time}ms")
        transaction_img.save("transaction_qr.png")