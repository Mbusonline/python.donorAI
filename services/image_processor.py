"""
Image Processing Service
Adds borders and processes images for reports
"""

import os
from typing import List, Optional

from PIL import Image, ImageOps

import settings


def add_border_to_image(
    image_path: str,
    output_path: str = None,
    border_width: Optional[int] = None,
    border_color: Optional[str] = None,
) -> str:
    """
    Add a border to an image.
    
    Args:
        image_path: Path to input image
        output_path: Path to save bordered image (if None, saves with _bordered suffix)
        border_width: Width of border in pixels (default: settings.IMAGE_BORDER_WIDTH)
        border_color: Border color in hex (default: settings.IMAGE_BORDER_COLOR)
    
    Returns:
        Path to the bordered image
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    if border_width is None:
        border_width = settings.IMAGE_BORDER_WIDTH
    if border_color is None:
        border_color = settings.IMAGE_BORDER_COLOR

    # Generate output path if not provided
    if output_path is None:
        base, ext = os.path.splitext(image_path)
        output_path = f"{base}_bordered{ext}"
    
    try:
        # Open image
        img = Image.open(image_path)
        
        # Convert to RGB if necessary (handles RGBA, P, etc.)
        if img.mode in ('RGBA', 'LA', 'P'):
            # Create white background for transparent images
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            if img.mode in ('RGBA', 'LA'):
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Add border using ImageOps.expand
        # This adds border around the image
        bordered_img = ImageOps.expand(
            img,
            border=border_width,
            fill=border_color
        )
        
        # Save bordered image
        bordered_img.save(output_path, quality=95)
        print(f"✓ Added border to image: {os.path.basename(output_path)}")
        
        return output_path
    
    except Exception as e:
        print(f"Warning: Could not add border to {image_path}: {str(e)}")
        # Return original path if border addition fails
        return image_path


def process_images_with_borders(
    image_paths: List[str],
    output_dir: str,
    border_width: Optional[int] = None,
    border_color: Optional[str] = None,
) -> List[str]:
    """
    Process multiple images to add borders.
    
    Args:
        image_paths: List of image file paths
        output_dir: Directory to save processed images
        border_width: Width of border in pixels
        border_color: Border color in hex format
    
    Returns:
        List of paths to processed images with borders
    """
    os.makedirs(output_dir, exist_ok=True)
    
    processed_images = []
    
    for img_path in image_paths:
        try:
            # Generate output path in output directory
            img_filename = os.path.basename(img_path)
            base, ext = os.path.splitext(img_filename)
            output_path = os.path.join(output_dir, f"{base}_bordered{ext}")
            
            # Add border
            bordered_path = add_border_to_image(
                image_path=img_path,
                output_path=output_path,
                border_width=border_width,
                border_color=border_color,
            )
            
            processed_images.append(bordered_path)
        
        except Exception as e:
            print(f"Warning: Could not process image {img_path}: {str(e)}")
            # Use original image if processing fails
            processed_images.append(img_path)
    
    return processed_images

