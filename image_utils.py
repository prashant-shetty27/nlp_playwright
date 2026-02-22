"""
Image comparison utilities for backend automation.

Functions:
    match_template(image_path, template_path, threshold=0.8):
        Returns True if template is found in image above threshold.
    compare_ssim(image_path1, image_path2, threshold=0.95):
        Returns True if images are similar above threshold.

Example usage:
    from image_utils import match_template, compare_ssim
    found = match_template('screen.png', 'button.png')
    similar = compare_ssim('expected.png', 'actual.png')
"""
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim

def match_template(image_path, template_path, threshold=0.8):
    """
    Uses OpenCV template matching to find the template in the image.
    Args:
        image_path (str): Path to the main image file.
        template_path (str): Path to the template image file.
        threshold (float): Similarity threshold (0-1), default 0.8.
    Returns:
        bool: True if match found above threshold, else False.
    Raises:
        ValueError: If image or template cannot be loaded.
    Example:
        >>> match_template('screen.png', 'button.png')
        True
    """
    img = cv2.imread(image_path, 0)
    template = cv2.imread(template_path, 0)
    if img is None or template is None:
        raise ValueError("Could not load image or template.")
    res = cv2.matchTemplate(img, template, cv2.TM_CCOEFF_NORMED)
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(res)
    return max_val >= threshold

def compare_ssim(image_path1, image_path2, threshold=0.95):
    """
    Uses SSIM to compare two images for similarity.
    Args:
        image_path1 (str): Path to the first image file.
        image_path2 (str): Path to the second image file.
        threshold (float): Similarity threshold (0-1), default 0.95.
    Returns:
        bool: True if similarity is above threshold, else False.
    Raises:
        ValueError: If either image cannot be loaded.
    Example:
        >>> compare_ssim('expected.png', 'actual.png')
        True
    """
    img1 = cv2.imread(image_path1, 0)
    img2 = cv2.imread(image_path2, 0)
    if img1 is None or img2 is None:
        raise ValueError("Could not load one or both images.")
    # Resize to match if needed
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    score, _ = ssim(img1, img2, full=True)
    return score >= threshold