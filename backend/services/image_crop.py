import os
import uuid
from typing import Dict, Any, Union, List, Tuple
from PIL import Image
from backend.config import settings

def compress_and_save_upload(image_file, output_dir: str = None) -> str:
    """
    Compress uploaded original image:
    Limit max long side to <= 2000px, JPEG quality 85.
    Saves to data/uploads/ and returns relative URL path.
    """
    if output_dir is None:
        output_dir = settings.UPLOAD_DIR
    os.makedirs(output_dir, exist_ok=True)

    filename = f"upload_{uuid.uuid4().hex[:12]}.jpg"
    filepath = os.path.join(output_dir, filename)

    # Open image
    img = Image.open(image_file)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    width, height = img.size
    max_side = max(width, height)
    if max_side > 2000:
        scale = 2000.0 / max_side
        new_w = int(width * scale)
        new_h = int(height * scale)
        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    img.save(filepath, "JPEG", quality=85, optimize=True)
    return f"/data/uploads/{filename}"

def crop_question_image(
    src_image_path: str,
    polygon_or_box: Union[List[int], Dict[str, Any]],
    output_dir: str = None
) -> str:
    """
    Crops a question region from src_image_path.
    polygon_or_box can be [x1, y1, x2, y2] or dict with X, Y, Width, Height or Points.
    Saves to data/cropped/ and returns relative URL path.
    """
    if output_dir is None:
        output_dir = settings.CROPPED_DIR
    os.makedirs(output_dir, exist_ok=True)

    # Resolve actual local path
    real_src = src_image_path
    if src_image_path.startswith("/data/"):
        real_src = src_image_path.lstrip("/")
    
    if not os.path.exists(real_src):
        # Fallback or create dummy if image not on disk
        return src_image_path

    try:
        img = Image.open(real_src)
        w, h = img.size

        # Parse coordinates
        if isinstance(polygon_or_box, list) and len(polygon_or_box) >= 4:
            x1, y1, x2, y2 = polygon_or_box[:4]
        elif isinstance(polygon_or_box, dict):
            if "X" in polygon_or_box and "Width" in polygon_or_box:
                x1 = polygon_or_box["X"]
                y1 = polygon_or_box["Y"]
                x2 = x1 + polygon_or_box["Width"]
                y2 = y1 + polygon_or_box["Height"]
            elif "x1" in polygon_or_box:
                x1 = polygon_or_box["x1"]
                y1 = polygon_or_box["y1"]
                x2 = polygon_or_box["x2"]
                y2 = polygon_or_box["y2"]
            elif "Points" in polygon_or_box:
                pts = polygon_or_box["Points"]
                xs = [p.get("X", 0) for p in pts]
                ys = [p.get("Y", 0) for p in pts]
                x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
            else:
                x1, y1, x2, y2 = 0, 0, w, h
        else:
            x1, y1, x2, y2 = 0, 0, w, h

        # Clamp boundaries
        x1 = max(0, min(int(x1), w - 1))
        y1 = max(0, min(int(y1), h - 1))
        x2 = max(x1 + 1, min(int(x2), w))
        y2 = max(y1 + 1, min(int(y2), h))

        cropped = img.crop((x1, y1, x2, y2))
        crop_filename = f"crop_{uuid.uuid4().hex[:12]}.jpg"
        crop_path = os.path.join(output_dir, crop_filename)
        cropped.save(crop_path, "JPEG", quality=85)
        return f"/data/cropped/{crop_filename}"
    except Exception as e:
        return src_image_path
