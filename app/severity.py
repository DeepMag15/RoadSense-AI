"""Severity estimation and road-condition scoring.

Each detection gets a 0-100 severity score built from:
  * class weight          - potholes / alligator cracking are structurally worse than linear cracks
  * extent                - perspective-corrected size (mask area for potholes, length for linear cracks)
  * verification      - pothole hypotheses are cross-checked by the segmentation model
  * auxiliary cue         - pothole depth (darkness contrast), crack width, alligator density
  * detector confidence   - a small damping term so uncertain boxes are not over-rated

Scores map to Minor (<30) / Moderate (30-59) / Critical (>=60).
An inspection-level Road Condition Index (RCI, 0-100, PCI-style) aggregates all detections.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from .config import CLASSES, SEVERITY_LEVELS

HORIZON_FRAC = 0.40  # typical dashcam horizon line as a fraction of image height


def perspective_scale(y_bottom: float, h: int, view: str) -> float:
    """How much farther-away objects are shrunk, relative to the bottom of a dashcam frame."""
    if view != "dashcam":
        return 1.0
    y_h = HORIZON_FRAC * h
    s = (h - y_h) / max(y_bottom - y_h, 0.08 * h)
    return float(min(max(s, 1.0), 4.0))


def _sat(x: float, k: float) -> float:
    """Saturating 0..1 curve: x == k gives ~0.63."""
    return 1.0 - math.exp(-max(x, 0.0) / k)


def pothole_depth_index(gray: np.ndarray, box, mask: np.ndarray | None) -> float:
    """Darkness of the pothole interior vs. a ring of surrounding pavement (0 = flat, 1 = deep/dark)."""
    h, w = gray.shape
    x1, y1, x2, y2 = [int(v) for v in box]
    x1, y1, x2, y2 = max(0, x1), max(0, y1), min(w, x2), min(h, y2)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return 0.0
    pad = int(0.35 * max(x2 - x1, y2 - y1))
    X1, Y1, X2, Y2 = max(0, x1 - pad), max(0, y1 - pad), min(w, x2 + pad), min(h, y2 + pad)
    region = gray[Y1:Y2, X1:X2].astype(np.float32)
    inner = np.zeros(region.shape, bool)
    if mask is not None and mask.shape == gray.shape:
        inner = mask[Y1:Y2, X1:X2] > 0
    if inner.sum() < 10:
        inner[y1 - Y1:y2 - Y1, x1 - X1:x2 - X1] = True
    ring = ~inner
    ring[y1 - Y1:y2 - Y1, x1 - X1:x2 - X1] = False
    if ring.sum() < 10:
        return 0.0
    inside, outside = float(np.median(region[inner])), float(np.median(region[ring]))
    return float(np.clip((outside - inside) / max(outside, 1.0) / 0.45, 0.0, 1.0))


def crack_metrics(gray: np.ndarray, box) -> tuple[float, float]:
    """Return (estimated crack width in px, crack-pixel density) using local dark-pixel thresholding."""
    x1, y1, x2, y2 = [int(v) for v in box]
    crop = gray[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    if crop.size < 64:
        return 0.0, 0.0
    crop = cv2.GaussianBlur(crop, (3, 3), 0)
    thr = cv2.adaptiveThreshold(crop, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV,
                                blockSize=max(15, (min(crop.shape) // 6) | 1), C=8)
    thr = cv2.morphologyEx(thr, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    density = float((thr > 0).mean())
    length = math.hypot(*crop.shape)
    width_px = float((thr > 0).sum()) / max(length, 1.0)
    return width_px, density


def score_detection(det: dict, img_w: int, img_h: int, gray: np.ndarray, view: str,
                    mask: np.ndarray | None = None) -> dict:
    """Compute severity score/level and supporting metrics for one detection (mutates & returns det)."""
    cls = det["cls"]
    x1, y1, x2, y2 = det["box"]
    bw, bh = max(x2 - x1, 1.0), max(y2 - y1, 1.0)
    frame_area = float(img_w * img_h)
    s = perspective_scale(y2, img_h, view)
    w = CLASSES[cls]["weight"]
    conf = float(det.get("conf", 1.0))
    metrics: dict = {"perspective_scale": round(s, 2)}

    if cls == "pothole":
        has_mask = mask is not None and mask.any()
        area_px = float(mask.sum()) if has_mask else 0.65 * bw * bh
        area_ratio = area_px / frame_area
        depth = pothole_depth_index(gray, det["box"], mask)
        seg_conf = float(det.get("seg_conf", 0.0))
        # second-opinion from the segmentation model verifies the pothole hypothesis
        # a second detector agreeing counts as strong verification too
        support = max(seg_conf if has_mask else 0.25, 0.8 if det.get("agreement") else 0.0)
        verify = 0.6 + 0.4 * support
        extent = _sat(area_ratio * s * s, 0.03)
        base = w * (0.2 + 0.6 * extent) * verify
        aux = 0.20 * depth
        metrics.update(area_pct=round(100 * area_ratio, 2), depth_index=round(depth, 2),
                       mask_used=bool(has_mask), seg_conf=round(seg_conf, 2))
    elif cls in ("longitudinal_crack", "transverse_crack"):
        length_ratio = math.hypot(bw, bh) / math.hypot(img_w, img_h)
        width_px, density = crack_metrics(gray, det["box"])
        width_rel = 100 * width_px * s / math.sqrt(frame_area)  # % of frame scale, perspective corrected
        extent = _sat(length_ratio * s, 0.35)
        base = w * (0.25 + 0.75 * extent)
        aux = 0.20 * _sat(width_rel, 1.0)
        metrics.update(length_pct=round(100 * length_ratio, 1), width_pct=round(width_rel, 2))
    elif cls == "alligator_crack":
        area_ratio = bw * bh / frame_area
        _, density = crack_metrics(gray, det["box"])
        extent = _sat(area_ratio * s * s, 0.10)
        base = w * (0.25 + 0.75 * extent)
        aux = 0.10 * min(density / 0.25, 1.0)
        metrics.update(area_pct=round(100 * area_ratio, 2), crack_density=round(density, 2))
    else:  # damaged_section: inherits from its worst member, escalated by defect count
        area_ratio = bw * bh / frame_area
        member_max = float(det.get("member_max_score", 30.0))
        score = round(min(100.0, member_max + 4.0 * (det.get("members", 2) - 1)), 1)
        metrics.update(area_pct=round(100 * area_ratio, 2), members=det.get("members", 0))
        det.update(score=score, severity=severity_level(score), metrics=metrics)
        return det

    raw = base * (0.85 + 0.15 * conf) + aux
    score = round(100 * min(raw, 1.0), 1)
    det["score"] = score
    det["severity"] = severity_level(score)
    det["metrics"] = metrics
    return det


def severity_level(score: float) -> str:
    for name, spec in SEVERITY_LEVELS.items():
        if score >= spec["min_score"]:
            return name
    return "Minor"


# ---------------------------------------------------------------- inspection level

RCI_BANDS = [(85, "Good"), (70, "Satisfactory"), (55, "Fair"), (40, "Poor"),
             (25, "Very Poor"), (10, "Serious"), (0, "Failed")]


def road_condition_index(scores: list[float], effective_frames: float) -> float:
    """PCI-style index: each defect deducts points with a convex penalty, normalised per view."""
    deduct = sum(40.0 * (sc / 100.0) ** 1.3 for sc in scores) / max(effective_frames, 1.0)
    return round(100.0 * math.exp(-deduct / 60.0), 1)


def rci_rating(rci: float) -> str:
    for lo, name in RCI_BANDS:
        if rci >= lo:
            return name
    return "Failed"


def repair_priority(severity_counts: dict, rci: float) -> str:
    if severity_counts.get("Critical", 0) or rci < 40:
        return "Immediate (within 7 days)"
    if severity_counts.get("Moderate", 0) or rci < 70:
        return "Short-term (within 30 days)"
    if severity_counts.get("Minor", 0):
        return "Routine (next maintenance cycle)"
    return "No action required"


RECOMMENDATIONS = {
    ("pothole", "Critical"): "Barricade and apply full-depth patch immediately",
    ("pothole", "Moderate"): "Semi-permanent hot-mix patching",
    ("pothole", "Minor"): "Throw-and-roll cold patch; monitor",
    ("alligator_crack", "Critical"): "Full-depth reclamation / base repair",
    ("alligator_crack", "Moderate"): "Partial-depth patch or mill & overlay",
    ("alligator_crack", "Minor"): "Surface seal (slurry / chip seal)",
    ("longitudinal_crack", "Critical"): "Mill & inlay along the crack line",
    ("longitudinal_crack", "Moderate"): "Rout and seal crack",
    ("longitudinal_crack", "Minor"): "Crack filling; monitor",
    ("transverse_crack", "Critical"): "Mill & inlay across the crack",
    ("transverse_crack", "Moderate"): "Rout and seal crack",
    ("transverse_crack", "Minor"): "Crack filling; monitor",
    ("damaged_section", "Critical"): "Resurface the full section",
    ("damaged_section", "Moderate"): "Mill & overlay the section",
    ("damaged_section", "Minor"): "Preventive surface treatment",
}


def recommendation(cls: str, severity: str) -> str:
    return RECOMMENDATIONS.get((cls, severity), "Inspect manually")
