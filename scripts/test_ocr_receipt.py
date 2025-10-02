import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Set

import cv2
import numpy as np
import pytesseract
from pytesseract import Output

# If needed on Windows, uncomment and adjust:
# pytesseract.pytesseract.tesseract_cmd = r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe"


IMAGE_PATH = (
    "/Users/nicolasschneider/Documents/Scans/2025-09-03_netto_1.jpeg"
)


def _deskew(gray: np.ndarray) -> np.ndarray:
    """Deskew the receipt slightly using the dominant text angle.

    Keeps it conservative to avoid warping: detects small rotation using Hough.
    """
    # Edge map and Hough lines to estimate skew
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLines(edges, 1, np.pi / 180.0, 200)
    if lines is None:
        return gray
    # Collect angles near horizontal; convert from radians
    angles: List[float] = []
    for rho_theta in lines[:100]:  # cap for speed
        _, theta = rho_theta[0]
        angle = (theta * 180.0 / np.pi) - 90.0  # near vertical text columns
        # We only correct small tilts
        if -5 <= angle <= 5:
            angles.append(angle)
    if not angles:
        return gray
    median_angle = float(np.median(angles))
    if abs(median_angle) < 0.2:
        return gray
    h, w = gray.shape[:2]
    M = cv2.getRotationMatrix2D((w // 2, h // 2), median_angle, 1.0)
    rotated = cv2.warpAffine(
        gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return rotated


def preprocess(path: str) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Load + enhance for OCR: upscale, denoise, contrast, binarize, deskew."""
    img = cv2.imread(path)
    assert img is not None, f"Could not read image: {path}"

    # Convert to gray
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    # Gentle denoise preserving edges
    gray = cv2.bilateralFilter(gray, 7, 60, 60)

    # Adaptive contrast using CLAHE (great for receipts)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    # Slight unsharp mask: sharpen text strokes
    blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
    gray = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)

    # Deskew small tilt
    gray = _deskew(gray)

    # Upscale to help Tesseract resolve small glyphs
    h, w = gray.shape[:2]
    scale = 2 if max(h, w) < 4000 else 1  # avoid huge images
    if scale != 1:
        gray = cv2.resize(gray, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)
        img = cv2.resize(img, (w * scale, h * scale), interpolation=cv2.INTER_CUBIC)

    # Adaptive threshold to normalize background; keep also a non-thresholded
    # version because Tesseract sometimes prefers grayscale.
    th = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )

    # Also produce a global Otsu binary which sometimes helps on thermal prints
    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # For compatibility with callers that expect three values, we pack Otsu
    # threshold back into a tuple via a small hack: we will return th (adaptive)
    # and reuse th_otsu later by recomputing from gray if needed.

    return img, gray, th


def _ocr_data(img: np.ndarray, config: str) -> Dict[str, List]:
    return pytesseract.image_to_data(img, config=config, output_type=Output.DICT)


def _draw_rect(canvas: np.ndarray, box: Tuple[int, int, int, int], color: Tuple[int, int, int], thickness: int = 2):
    x, y, w, h = box
    cv2.rectangle(canvas, (x, y), (x + w, y + h), color, thickness)


def _normalize_text(t: str) -> str:
    t = t.strip().lower()
    t = t.replace("€", "e").replace(" ", "")
    return t


def _iou(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    xa1, ya1, xa2, ya2 = ax, ay, ax + aw, ay + ah
    xb1, yb1, xb2, yb2 = bx, by, bx + bw, by + bh
    inter_w = max(0, min(xa2, xb2) - max(xa1, xb1))
    inter_h = max(0, min(ya2, yb2) - max(ya1, yb1))
    inter = inter_w * inter_h
    if inter == 0:
        return 0.0
    area_a = aw * ah
    area_b = bw * bh
    return inter / float(area_a + area_b - inter + 1e-6)


def _collect_detections(
    data: Dict[str, List],
    conf_threshold: int,
    x_offset: int = 0,
    kind: str = "word",
    price_only: bool = False,
    restrict_to_words: Optional[Set[str]] = None,
    source: str = "general",
) -> List[Dict]:
    dets: List[Dict] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        try:
            conf = int(float(data["conf"][i]))
        except Exception:
            conf = -1
        if conf < conf_threshold or not text:
            continue
        if price_only and not PRICE_REGEX.search(text):
            continue
        norm = _normalize_text(text)
        if restrict_to_words is not None and norm not in restrict_to_words:
            continue
        x, y, w, h = (
            int(data["left"][i]) + int(x_offset),
            int(data["top"][i]),
            int(data["width"][i]),
            int(data["height"][i]),
        )
        dets.append({
            "text": text,
            "norm": norm,
            "conf": conf,
            "box": (x, y, w, h),
            "kind": kind,
            "source": source,
        })
    return dets


def _merge_detections(dets: List[Dict]) -> List[Dict]:
    # Group by normalized text to collapse duplicates across passes
    groups: Dict[str, List[Dict]] = {}
    for d in dets:
        groups.setdefault(d["norm"], []).append(d)

    merged: List[Dict] = []
    for norm, g in groups.items():
        # Non-maximum suppression by IoU within the group
        g = sorted(g, key=lambda x: x["conf"], reverse=True)
        kept: List[Dict] = []
        for cand in g:
            ok = True
            for k in kept:
                if _iou(cand["box"], k["box"]) > 0.4:
                    ok = False
                    break
            if ok:
                kept.append(cand)
        # Prefer price kind if any entry looks like a price
        for k in kept:
            if PRICE_REGEX.search(k["text"]):
                k["kind"] = "price"
        merged.extend(kept)
    return merged


PRICE_REGEX = re.compile(
    r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3})*|\d+)[.,]\d{2}(?!\d)"
)


def _collect_words(data: Dict[str, List], min_conf: int = 50) -> List[Dict]:
    """Collect high-confidence words with their boxes."""
    words: List[Dict] = []
    n = len(data.get("text", []))
    for i in range(n):
        text = (data["text"][i] or "").strip()
        try:
            conf = int(float(data["conf"][i]))
        except Exception:
            conf = -1
        if not text or conf < min_conf:
            continue
        x, y, w, h = (
            int(data["left"][i]),
            int(data["top"][i]),
            int(data["width"][i]),
            int(data["height"][i]),
        )
        words.append({
            "text": text,
            "conf": conf,
            "x": x,
            "y": y,
            "w": w,
            "h": h,
            "block": int(data.get("block_num", [0])[i]) if "block_num" in data else 0,
            "par": int(data.get("par_num", [0])[i]) if "par_num" in data else 0,
            "line": int(data.get("line_num", [0])[i]) if "line_num" in data else 0,
        })
    return words


def _group_lines(words: List[Dict]) -> List[List[Dict]]:
    """Group OCR words into visual lines using y-centers and median height."""
    if not words:
        return []

    heights = [w["h"] for w in words]
    median_h = int(np.median(heights)) or 10
    y_tol = max(8, int(median_h * 0.6))

    # Prefer Tesseract's block/line grouping when available
    if all(k in words[0] for k in ("block", "par", "line")):
        grouped: Dict[Tuple[int, int, int], List[Dict]] = {}
        for w in words:
            key = (w.get("block", 0), w.get("par", 0), w.get("line", 0))
            grouped.setdefault(key, []).append(w)
        return [sorted(v, key=lambda r: r["x"]) for v in grouped.values()]

    # Fallback: Sort by y then x using tolerance
    words.sort(key=lambda r: (r["y"], r["x"]))

    lines: List[List[Dict]] = []
    current: List[Dict] = []
    for w in words:
        if not current:
            current = [w]
            continue
        last_y = int(np.mean([t["y"] for t in current]))
        if abs(w["y"] - last_y) <= y_tol:
            current.append(w)
        else:
            lines.append(sorted(current, key=lambda r: r["x"]))
            current = [w]
    if current:
        lines.append(sorted(current, key=lambda r: r["x"]))
    return lines


def extract_item_price_pairs(data: Dict[str, List]) -> List[Tuple[str, str]]:
    pairs: List[Tuple[str, str]] = []
    words = _collect_words(data, min_conf=45)
    lines = _group_lines(words)
    if not words:
        return pairs
    page_right = max(w["x"] + w["w"] for w in words)
    skip_line_if_contains = {
        "summe",
        "sumne",
        "rückgeld",
        "rueckgeld",
        "ruckgeld",
        "ec-cash",
        "ec",
        "kunden-beleg",
        "kundenbeleg",
        "kartenzahlung",
        "zahlung",
        "mwst",
        "netto",
        "brutto",
        "satz",
        "eur",
        "uhr",
        "datum",
        "terminal",
        "ta-nr",
        "bnr",
        "genehmigungs-nr",
        "girocard",
        "expert",
        "famila",
        "bielefeld",
    }

    # Index price-like tokens for nearest-right fallback
    price_tokens: List[Dict] = []
    for w in words:
        if PRICE_REGEX.search(w["text"]):
            price_tokens.append(w)

    for line in lines:
        texts = [w["text"] for w in line]
        norm_line = " ".join(texts).lower()
        if any(key in norm_line for key in skip_line_if_contains):
            continue

        # Find rightmost price-like token
        price_idx = None
        for idx in reversed(range(len(line))):
            if PRICE_REGEX.search(line[idx]["text"]):
                price_idx = idx
                break
        if price_idx is None:
            # Nearest-right price fallback by y proximity
            if not price_tokens:
                continue
            y_center = int(np.median([w["y"] + w["h"] // 2 for w in line]))
            x_right = max(w["x"] + w["w"] for w in line)
            line_h = int(np.median([w["h"] for w in line]))
            y_tol = max(10, int(line_h * 0.9))
            best = None
            best_dx = 10**9
            for p in price_tokens:
                py = p["y"] + p["h"] // 2
                if abs(py - y_center) <= y_tol and p["x"] >= x_right - 5:
                    dx = p["x"] - x_right
                    if dx < best_dx:
                        best = p
                        best_dx = dx
            if best is None:
                continue
            price_word = best
        else:
            price_word = line[price_idx]
            # Guard: ignore unit/weight numbers in the middle
            if price_word["x"] < page_right * 0.55:
                continue
        price = price_word["text"]

        # Filter out obvious non-item lines
        if "%" in norm_line:
            continue

        # Product name is everything left of the price on the same line
        name_tokens = [
            w["text"] for w in line[:price_idx] if not PRICE_REGEX.search(w["text"])
        ]
        # Heuristics: drop leading quantity like "(2 x 1,29)" in name
        name = " ".join(name_tokens).strip("-–··:;,.()[] ")
        # Skip ultra-short leftovers
        if len(name) < 3 or " x " in name.lower():
            continue
        pairs.append((name, price))

    return pairs


def _estimate_price_rois(data_general: Dict[str, List], width: int) -> List[int]:
    """Estimate starting x coordinates for price column ROIs.

    Uses the distribution of price-like tokens in the initial general OCR pass.
    Falls back to a couple of generic starts if insufficient evidence.
    """
    xs: List[int] = []
    n = len(data_general.get("text", []))
    for i in range(n):
        t = (data_general["text"][i] or "").strip()
        if not t or not PRICE_REGEX.search(t):
            continue
        xs.append(int(data_general["left"][i]))
    if len(xs) >= 3:
        x_thresh = int(np.percentile(xs, 60))  # ignore left-side numbers
        margin = int(width * 0.06)
        starts = sorted({max(0, x_thresh - margin), max(0, int(width * 0.55))})
    else:
        starts = [int(width * 0.5), int(width * 0.6)]
    return starts


def run_pipeline(image_path: str) -> None:
    original, gray, th = preprocess(image_path)
    # Additional global binary
    _, th_otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # 1) General text pass: treat as a single block of text (psm 6)
    config_general = r"--oem 3 --psm 6 -c user_defined_dpi=300 -l deu+eng"
    data_general = _ocr_data(gray, config_general)

    # 2) Price-focused pass on the thresholded version with a whitelist
    config_prices = r"--oem 3 --psm 11 -c user_defined_dpi=300 -c tessedit_char_whitelist=0123456789,.-€ -c classify_bln_numeric_mode=1"
    data_prices = _ocr_data(th, config_prices)

    # 3) Extra passes on the right column (auto-detected starts)
    h, w = th.shape[:2]
    x_starts = _estimate_price_rois(data_general, w)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    config_roi = r"--oem 3 --psm 6 -c user_defined_dpi=300 -c tessedit_char_whitelist=0123456789,.-€ -c classify_bln_numeric_mode=1"
    config_roi2 = r"--oem 3 --psm 4 -c user_defined_dpi=300 -c tessedit_char_whitelist=0123456789,.-€ -c classify_bln_numeric_mode=1"
    roi_passes: List[Tuple[int, Dict[str, List]]] = []
    roi_passes2: List[Tuple[int, Dict[str, List]]] = []
    for x0 in x_starts:
        roi = th[:, x0:]
        roi_dil = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, kernel, iterations=1)
        roi_passes.append((x0, _ocr_data(roi_dil, config_roi)))
        roi_gray = gray[:, x0:]
        roi_passes2.append((x0, _ocr_data(roi_gray, config_roi2)))

    # Collect detections for de-duplication across passes
    dets: List[Dict] = []
    dets += _collect_detections(data_general, 30, kind="word", price_only=False, source="general_psm6")
    dets += _collect_detections(data_prices, 15, kind="price", price_only=True, source="prices_psm11_bin")
    for x0, d in roi_passes:
        dets += _collect_detections(d, 15, x_offset=x0, kind="price", price_only=True, source=f"roi_bin_psm6_{x0}")
    for x0, d in roi_passes2:
        dets += _collect_detections(d, 20, x_offset=x0, kind="price", price_only=True, source=f"roi_gray_psm4_{x0}")

    # Whole-page column-mode + sparse-text to recover weak words (generic, high confidence only)
    data_psm4 = _ocr_data(gray, r"--oem 3 --psm 4 -l deu+eng -c user_defined_dpi=300")
    dets += _collect_detections(data_psm4, 70, kind="word", source="psm4_gray")
    data_sparse = _ocr_data(gray, r"--oem 3 --psm 12 -l deu+eng -c user_defined_dpi=300")
    dets += _collect_detections(data_sparse, 75, kind="word", source="psm12_sparse")

    # Extra: try a binarized Otsu pass for item names that can be faint
    data_psm5_bin = _ocr_data(th_otsu, r"--oem 3 --psm 5 -l deu+eng -c user_defined_dpi=300")
    dets += _collect_detections(data_psm5_bin, 40, kind="word", source="psm5_otsu")
    # And a standard grayscale psm 5
    data_psm5_gray = _ocr_data(gray, r"--oem 3 --psm 5 -l deu+eng -c user_defined_dpi=300")
    dets += _collect_detections(data_psm5_gray, 45, kind="word", source="psm5_gray")

    # Merge duplicates and draw a single box per token
    merged = _merge_detections(dets)
    canvas = original.copy()
    for d in merged:
        color = (0, 200, 0) if d["kind"] != "price" else (0, 165, 255)
        _draw_rect(canvas, d["box"], color, thickness=2 if d["kind"] != "price" else 3)

    # Validation: ensure each visual line has a price box to the right
    line_words = _collect_words(data_general, min_conf=40)
    lines = _group_lines(line_words)
    price_boxes = [t["box"] for t in merged if t["kind"] == "price"]
    validation_misses: List[Dict] = []
    for line in lines:
        if not line:
            continue
        y_center = int(np.median([w["y"] + w["h"] // 2 for w in line]))
        x_right = max(w["x"] + w["w"] for w in line)
        line_h = int(np.median([w["h"] for w in line]))
        y_tol = max(10, int(line_h * 0.9))
        found = False
        for bx, by, bw, bh in price_boxes:
            py = by + bh // 2
            if abs(py - y_center) <= y_tol and bx >= x_right - 5:
                found = True
                break
        if not found:
            validation_misses.append({
                "y_center": y_center,
                "x_right": x_right,
                "line_text": " ".join(w["text"] for w in line),
            })
            # Draw a small red square where a right-price would be expected
            _draw_rect(canvas, (x_right + 10, y_center - 6, 12, 12), (0, 0, 255), 2)

    # Extract item-price pairs from the richer general pass (unchanged)
    pairs = extract_item_price_pairs(data_general)

    # Write annotated image
    out_img = Path("receipt_boxes_fixed.jpg")
    cv2.imwrite(str(out_img), canvas)

    # Export tokens and validation to JSON for debugging
    out_dir = Path("enhanced_receipt_outputs")
    out_dir.mkdir(exist_ok=True)
    tokens_json = out_dir / "tokens.json"
    def _ser(det: Dict) -> Dict:
        x, y, w2, h2 = det["box"]
        return {
            "text": det["text"],
            "norm": det["norm"],
            "conf": det["conf"],
            "kind": det["kind"],
            "source": det.get("source", ""),
            "x": x,
            "y": y,
            "w": w2,
            "h": h2,
        }
    import json
    with tokens_json.open("w", encoding="utf-8") as f:
        json.dump({
            "image_path": image_path,
            "tokens_merged": [_ser(d) for d in merged],
            "detections_raw": [_ser(d) for d in dets],
            "validation": {
                "right_price_misses": validation_misses,
                "miss_count": len(validation_misses),
            },
        }, f, ensure_ascii=False, indent=2)

    # Print extracted pairs to console
    print("Extracted items and prices (heuristic):")
    for name, price in pairs:
        print(f"- {name} -> {price}")

    # Also save to a small CSV for quick inspection
    out_csv = out_dir / "extracted_items.csv"
    with out_csv.open("w", encoding="utf-8") as f:
        f.write("item,price\n")
        for name, price in pairs:
            # Escape quotes and commas minimally
            safe_name = name.replace('"', "''")
            f.write(f'"{safe_name}",{price}\n')


if __name__ == "__main__":
    run_pipeline(IMAGE_PATH)
