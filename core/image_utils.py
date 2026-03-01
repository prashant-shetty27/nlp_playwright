"""
core/image_utils.py
Image comparison utilities for backend automation.
"""
import cv2
from skimage.metrics import structural_similarity as ssim


def match_template(image_path: str, template_path: str, threshold: float = 0.8) -> bool:
    """Returns True if template is found in image above threshold."""
    img = cv2.imread(image_path, 0)
    template = cv2.imread(template_path, 0)
    if img is None or template is None:
        raise ValueError("Could not load image or template.")
    res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, _ = cv2.minMaxLoc(res)
    return max_val >= threshold


def compare_ssim(image_path1: str, image_path2: str, threshold: float = 0.95) -> bool:
    """Returns True if two images are similar above threshold."""
    img1 = cv2.imread(image_path1, 0)
    img2 = cv2.imread(image_path2, 0)
    if img1 is None or img2 is None:
        raise ValueError("Could not load one or both images.")
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    score, _ = ssim(img1, img2, full=True)
    return score >= threshold
