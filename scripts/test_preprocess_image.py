# receipt_enhance.py
# Minimal, no-argparse pipeline to make receipts LLM/OCR-friendly.

import os
from pathlib import Path
import numpy as np
import cv2

# === Set your input image here ===
INPUT_PATH = "/Users/nicolasschneider/Documents/Scans/1970-01-01_familia_betreff_1.jpeg"  # change as needed
OUT_DIR = Path("enhanced_receipt_outputs")

# enhance_receipt_angle_strict.py
# Goal: keep product names aligned with their prices by optimizing for column structure.
# No argparse; single-file script.


# ---------- IO helpers ----------
def read_image_with_exif(path: str):
    """Load image and honor EXIF orientation if Pillow is available."""
    try:
        from PIL import Image, ImageOps
        im = Image.open(path)
        im = ImageOps.exif_transpose(im)
        bgr = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
        return bgr
    except Exception:
        data = np.fromfile(path, dtype=np.uint8)
        img = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return img

def imwrite_unicode(path: str, img, params=None):
    ext = os.path.splitext(str(path))[1]
    ok, buf = cv2.imencode(ext, img, params or [])
    if not ok:
        raise RuntimeError(f"cv2.imencode failed for {path}")
    buf.tofile(str(path))

def ensure_dir(d: Path): d.mkdir(parents=True, exist_ok=True)
def save(tag: str, img, params=None):
    out = OUT_DIR / f"{tag}.png"
    imwrite_unicode(out, img, params)
    print(f"[saved] {out}")

# ---------- Geometry / orientation ----------
def rotate90(bgr, k):
    if k % 4 == 0: return bgr
    if k % 4 == 1: return cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
    if k % 4 == 2: return cv2.rotate(bgr, cv2.ROTATE_180)
    return cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)

def to_gray(bgr): return cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

def binary_for_score(gray):
    # Conservative local threshold to preserve faint ink
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 15)

def horiz_line_score(bw):
    # Row structure metric
    ink = (255 - bw).astype(np.uint8)
    row_sum = ink.sum(axis=1).astype(np.float32)
    return float(row_sum.std())

def vert_column_score(bw):
    # Column structure metric (critical for price alignment)
    ink = (255 - bw).astype(np.uint8)
    col_sum = ink.sum(axis=0).astype(np.float32)
    # Smooth a bit so peaks dominate
    col_sum = cv2.GaussianBlur(col_sum.reshape(1, -1), (1, 51), 0).ravel()
    # Peakiness: variance + mean absolute diff
    var = float(col_sum.var())
    mad = float(np.mean(np.abs(np.diff(col_sum))))
    return var + 0.5 * mad

def auto_orient_0_90_180_270(bgr):
    cands = [rotate90(bgr, k) for k in range(4)]
    scores = []
    for k, img in enumerate(cands):
        bw = binary_for_score(to_gray(img))
        score = horiz_line_score(bw) + vert_column_score(bw)
        scores.append((score, k))
    _, best_k = max(scores)
    return rotate90(bgr, best_k), best_k

def warp_affine_keep_white(img, M, size):
    return cv2.warpAffine(
        img, M, size, flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255)
    )

def rotate_image(img, angle_deg):
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w/2, h/2), angle_deg, 1.0)
    return warp_affine_keep_white(img, M, (w, h))

def downscale_long_edge(gray, max_long=1600):
    h, w = gray.shape[:2]
    long = max(h, w)
    if long <= max_long:
        return gray, 1.0
    scale = max_long / float(long)
    out = cv2.resize(gray, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_AREA)
    return out, scale

def optimize_rotation_for_grid(gray_full, search_range=4.0, coarse_step=0.5, fine_step=0.1):
    """
    Find the small rotation angle that maximizes combined row+column structure.
    This keeps right-aligned prices in a crisp vertical stack.
    """
    # Work on a downscaled thumbnail for speed
    gray, _ = downscale_long_edge(gray_full, max_long=1600)

    def score_for_angle(a):
        r = rotate_image(gray, a)
        bw = binary_for_score(r)
        # Weight columns higher than rows; names↔prices is column-sensitive.
        return 0.6 * vert_column_score(bw) + 0.4 * horiz_line_score(bw)

    # Coarse search
    best_a, best_s = 0.0, -1e18
    angles = np.arange(-search_range, search_range + 1e-6, coarse_step)
    for a in angles:
        s = score_for_angle(a)
        if s > best_s:
            best_a, best_s = a, s

    # Fine search around the best coarse angle
    fine_lo = max(-search_range, best_a - 1.0)
    fine_hi = min(search_range, best_a + 1.0)
    angles_fine = np.arange(fine_lo, fine_hi + 1e-6, fine_step)
    for a in angles_fine:
        s = score_for_angle(a)
        if s > best_s:
            best_a, best_s = a, s

    return float(best_a)

def force_portrait(bgr):
    h, w = bgr.shape[:2]
    return rotate90(bgr, 1) if w > h else bgr

# ---------- Enhancement pipeline ----------
def tight_crop(gray):
    bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    inv = cv2.bitwise_not(bw)
    ys, xs = np.where(inv > 0)
    if len(xs) == 0: return gray
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    pad = int(0.01 * max(gray.shape))
    x0 = max(0, x0 - pad); y0 = max(0, y0 - pad)
    x1 = min(gray.shape[1]-1, x1 + pad); y1 = min(gray.shape[0]-1, y1 + pad)
    return gray[y0:y1+1, x0:x1+1]

def illumination_correct(gray):
    bg = cv2.medianBlur(gray, 31)
    return cv2.divide(gray, bg, scale=255)

def clahe(gray):
    c = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    return c.apply(gray)

def denoise_sharpen(gray):
    den = cv2.fastNlMeansDenoising(gray, h=8, templateWindowSize=7, searchWindowSize=21)
    blur = cv2.GaussianBlur(den, (0, 0), 1.0)
    return cv2.addWeighted(den, 1.6, blur, -0.6, 0)

def safe_binary(gray):
    ada = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                cv2.THRESH_BINARY, 31, 15)
    if (ada == 255).mean() > 0.98:
        otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        if (otsu == 255).mean() > 0.98:
            otsu = cv2.bitwise_not(otsu)
        return otsu
    return ada

def morph_clean(bw):
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    opened = cv2.morphologyEx(bw, cv2.MORPH_OPEN, k, iterations=1)
    closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, k, iterations=1)
    return closed

def upscale_min_width(img, target_w=1800):
    h, w = img.shape[:2]
    if w >= target_w: return img
    scale = target_w / float(w)
    return cv2.resize(img, (int(w*scale), int(h*scale)), interpolation=cv2.INTER_CUBIC)

# ---------- Debug overlay for column peaks (to verify price alignment) ----------
def draw_column_guides(gray_or_bw, base_bgr):
    if len(gray_or_bw.shape) == 3:
        gray = cv2.cvtColor(gray_or_bw, cv2.COLOR_BGR2GRAY)
    else:
        gray = gray_or_bw
    bw = safe_binary(gray)
    ink = (255 - bw).astype(np.uint8)
    col_sum = ink.sum(axis=0).astype(np.float32)
    col_sum = cv2.GaussianBlur(col_sum.reshape(1, -1), (1, 51), 0).ravel()
    # pick top 3 column peaks
    peaks = np.argsort(col_sum)[-3:]
    overlay = base_bgr.copy()
    h, w = overlay.shape[:2]
    for x in sorted(peaks):
        cv2.line(overlay, (int(x), 0), (int(x), h-1), (40, 170, 255), 2)  # orange guide
    return overlay

# ---------- Main ----------
def main():
    ensure_dir(OUT_DIR)

    bgr = read_image_with_exif(INPUT_PATH)
    if bgr is None:
        raise FileNotFoundError(f"Could not load image: {INPUT_PATH}")
    print(f"[info] loaded: {INPUT_PATH} shape={bgr.shape}")

    # 0) Coarse orientation and portrait
    bgr, k = auto_orient_0_90_180_270(bgr)
    bgr = force_portrait(bgr)
    save("01_oriented_bgr", bgr)

    # 1) Angle optimization for column integrity
    gray0 = to_gray(bgr)
    best_angle = optimize_rotation_for_grid(gray0, search_range=4.0, coarse_step=0.5, fine_step=0.1)
    print(f"[info] best small-angle for grid = {best_angle:.2f}°")
    bgr = rotate_image(bgr, best_angle)
    save("02_angle_optimized_bgr", bgr)

    # 2) Enhancement pipeline
    gray = to_gray(bgr);                           save("03_gray", gray)
    gray = tight_crop(gray);                       save("04_gray_cropped", gray)
    illum = illumination_correct(gray);            save("05_illum_corrected", illum)
    hi = clahe(illum);                             save("06_clahe", hi)
    clean = denoise_sharpen(hi);                   save("07_denoise_sharpen", clean)
    bw = safe_binary(clean);                       save("08_binary_raw", bw)
    bw = morph_clean(bw);                          save("09_binary_clean", bw)

    # 3) Upscale deliverables
    gray_out = upscale_min_width(clean, 1800);     save("10_gray_for_llm", gray_out,
                                                        params=[cv2.IMWRITE_PNG_COMPRESSION, 9])
    bw_out = upscale_min_width(bw, 1800);          save("11_binary_for_llm", bw_out,
                                                        params=[cv2.IMWRITE_PNG_COMPRESSION, 9])

    # 4) Visual check: are price columns vertical?
    overlay = draw_column_guides(gray_out, cv2.cvtColor(gray_out, cv2.COLOR_GRAY2BGR))
    save("12_overlay_column_guides", overlay)

    print("\n[done] Outputs in:", OUT_DIR.resolve())
    print("Feed 10_gray_for_llm.png (grayscale) or 11_binary_for_llm.png (binary) to your LLM.")
    print("Use 12_overlay_column_guides.png to sanity-check price column alignment.")

if __name__ == "__main__":
    main()