"""Two-stage road damage inference.

Stage 1  YOLOv8s fine-tuned on RDD2022 (dronefreak/rdd2022-yolov8s)
         -> longitudinal / transverse / alligator cracks, potholes (dashcam viewpoints only)
         + general pothole detector (Harisanth/Pothole-Finetuned-YOLOv8)
         -> potholes in close-up, handheld and web photos that RDD2022 misses
Stage 2  YOLOv8s-seg pothole model (keremberke/yolov8s-pothole-segmentation), run on *crops*
         of stage-1 potholes -> pixel masks for area & depth estimation, plus filtered
         full-frame proposals.
Stage 3  Spatial clustering of co-located defects -> "damaged road section" regions.
Stage 4  Severity scoring (severity.py).
"""
from __future__ import annotations

import shutil
import threading

import cv2
import numpy as np
import torch
from ultralytics import YOLO

from . import severity as sev
from .config import CLASSES, DEFAULTS, DETECTOR, POTHOLE_DETECTOR, SEGMENTER, SEVERITY_LEVELS


def _ensure_weights(spec: dict):
    if not spec["path"].exists():
        from huggingface_hub import hf_hub_download
        spec["path"].parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(hf_hub_download(spec["repo"], spec["file"]), spec["path"])
    return str(spec["path"])


def _hex_to_bgr(h: str):
    h = h.lstrip("#")
    return int(h[4:6], 16), int(h[2:4], 16), int(h[0:2], 16)


class RoadDamageDetector:
    def __init__(self):
        self.device = 0 if torch.cuda.is_available() else "cpu"
        self.det = YOLO(_ensure_weights(DETECTOR))
        self.pd = YOLO(_ensure_weights(POTHOLE_DETECTOR))
        self.seg = YOLO(_ensure_weights(SEGMENTER))
        self.det_names = self.det.names
        self.lock = threading.Lock()  # ultralytics predictors are not thread-safe

    # ------------------------------------------------------------------ public
    def info(self) -> dict:
        return {
            "device": "cuda" if self.device == 0 else "cpu",
            "detector": DETECTOR["repo"],
            "pothole_detector": POTHOLE_DETECTOR["repo"],
            "segmenter": SEGMENTER["repo"],
            "detector_classes": list(self.det_names.values()),
        }

    @staticmethod
    def scene_type(frame: np.ndarray) -> str:
        """'dashcam' if the top of the frame shows sky (street-level view), else 'topdown'
        (close-up / handheld / drone photo looking down at the pavement)."""
        h = frame.shape[0]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        top, bottom = hsv[: max(1, int(0.3 * h))], hsv[int(0.7 * h):]
        hue, sat, val = (top[..., i].astype(np.int16) for i in range(3))
        # saturated blue sky (a cold, bluish-grey road is not saturated enough to count)
        blue_sky = float(((hue >= 90) & (hue <= 130) & (sat > 60) & (val > 120)).mean())
        # overcast sky: bright and grey, and clearly brighter than the road at the bottom
        white_sky = float(((val > 190) & (sat < 45)).mean())
        gap = float(np.median(top[..., 2])) - float(np.median(bottom[..., 2]))
        return "dashcam" if blue_sky >= 0.2 or (white_sky >= 0.3 and gap >= 40) else "topdown"

    def analyze(self, frame: np.ndarray, conf: float = DEFAULTS["conf"], view: str = DEFAULTS["view"],
                use_seg: bool = True) -> list[dict]:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if view not in ("dashcam", "topdown"):
            view = self.scene_type(frame)
        with self.lock:
            r = self.det.predict(frame, conf=conf, iou=DEFAULTS["iou"], imgsz=DEFAULTS["imgsz"],
                                 device=self.device, verbose=False)[0]
        dets = []
        for box, c, p in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.cls.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            x1, y1, x2, y2 = [float(v) for v in box]
            if view == "dashcam" and y2 < 0.36 * h:  # above the horizon: sky / buildings
                continue
            dets.append({"cls": self.det_names[int(c)], "conf": round(float(p), 3),
                         "box": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)], "sources": ["RDD2022"]})
        self._add_pothole_detector(frame, dets, view)

        dets = [d for d in dets if not (d["cls"] == "pothole" and _is_road_marking(gray, d["box"]))]
        masks = self._segment_potholes(frame, dets) if use_seg else {}
        # Large pothole boxes that are flat (interior no darker than surrounding pavement) or that the
        # segmenter cannot confirm are almost always crosswalks, patches or shadows. Only applied to
        # street-level views: in close-ups, sunlit potholes are often no darker than the pavement.
        keep = []
        for i, d in enumerate(dets):
            x1, y1, x2, y2 = d["box"]
            if d["cls"] == "pothole" and view == "dashcam":
                area_frac = (x2 - x1) * (y2 - y1) / (w * h)
                mask, seg_conf = masks.get(i, (None, 0.0))
                flat = sev.pothole_depth_index(gray, d["box"], mask) < 0.12
                if (area_frac > 0.03 and flat) or (area_frac > 0.12 and seg_conf < 0.35):
                    continue
            keep.append(i)
        masks = {keep.index(i): masks[i] for i in keep if i in masks}
        dets = [dets[i] for i in keep]

        # The RDD2022 detector only knows dashcam viewpoints and misses most close-up potholes, so the
        # segmenter also proposes potholes on the full frame (filtered to reject non-road hallucinations).
        if use_seg:
            for mask, box, seg_conf in self._full_frame_potholes(frame, gray, dets, view):
                masks[len(dets)] = (mask, seg_conf)
                dets.append({"cls": "pothole", "conf": round(seg_conf, 3), "box": box, "sources": ["segmenter"]})

        sections = self._damaged_sections(dets, w, h)

        for i, d in enumerate(dets):
            if d["cls"] == "damaged_section":
                continue
            m, seg_conf = masks.get(i, (None, 0.0))
            d["seg_conf"] = round(seg_conf, 3)
            sev.score_detection(d, w, h, gray, view, mask=m)
            sources = d.pop("sources", [])
            if m is not None and m.any() and "segmenter" not in sources:
                sources.append("segmenter")
            d["metrics"]["detected_by"] = ", ".join(sources)
            d["metrics"]["scene"] = "street" if view == "dashcam" else "close-up"
            if m is not None and m.any():
                d["polygon"] = _mask_to_polygon(m)
        for sec in sections:
            sec["member_max_score"] = max(dets[m]["score"] for m in sec.pop("member_idx"))
            sev.score_detection(sec, w, h, gray, view)
        dets += sections
        for d in dets:
            d["recommendation"] = sev.recommendation(d["cls"], d["severity"])
        dets.sort(key=lambda d: -d["score"])
        return dets

    # ----------------------------------------------------------------- stages
    def _segment_potholes(self, frame, dets) -> dict[int, tuple[np.ndarray, float]]:
        h, w = frame.shape[:2]
        idx = [i for i, d in enumerate(dets) if d["cls"] == "pothole"]
        if not idx:
            return {}
        crops, offs = [], []
        for i in idx:
            x1, y1, x2, y2 = dets[i]["box"]
            px, py = 0.15 * (x2 - x1), 0.15 * (y2 - y1)
            X1, Y1 = int(max(0, x1 - px)), int(max(0, y1 - py))
            X2, Y2 = int(min(w, x2 + px)), int(min(h, y2 + py))
            crops.append(frame[Y1:Y2, X1:X2])
            offs.append((X1, Y1, X2, Y2))
        with self.lock:
            results = self.seg.predict(crops, conf=0.15, imgsz=320, device=self.device, verbose=False, retina_masks=True)
        out = {}
        for i, (X1, Y1, X2, Y2), res in zip(idx, offs, results):
            if res.masks is None or len(res.masks) == 0:
                continue
            # union of all predicted masks in the crop, clipped to the detector box
            m = (res.masks.data.cpu().numpy() > 0.5).any(axis=0).astype(np.uint8)
            m = cv2.resize(m, (X2 - X1, Y2 - Y1), interpolation=cv2.INTER_NEAREST)
            full = np.zeros((h, w), np.uint8)
            full[Y1:Y2, X1:X2] = m
            x1, y1, x2, y2 = [int(v) for v in dets[i]["box"]]
            clip = np.zeros_like(full)
            clip[y1:y2, x1:x2] = 1
            full &= clip
            box_area = max((x2 - x1) * (y2 - y1), 1)
            if full.sum() > 0.08 * box_area:  # reject tiny/degenerate masks
                out[i] = (full, float(res.boxes.conf.max()))
        return out

    def _add_pothole_detector(self, frame, dets, view):
        """Merge potholes from the general pothole detector into the RDD2022 detections (in place)."""
        h = frame.shape[0]
        street = view == "dashcam"
        with self.lock:
            r = self.pd.predict(frame, conf=0.5 if street else 0.3, iou=DEFAULTS["iou"], imgsz=DEFAULTS["imgsz"],
                                device=self.device, verbose=False)[0]
        for box, p in zip(r.boxes.xyxy.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            box = [round(float(v), 1) for v in box]
            if street and box[3] < 0.36 * h:
                continue
            same = next((d for d in dets if d["cls"] == "pothole" and _overlap(box, d["box"]) > 0.4), None)
            if same is not None:  # both detectors agree: one pothole, higher confidence
                same["conf"] = round(max(same["conf"], float(p)), 3)
                same["sources"].append("pothole detector")
                same["agreement"] = True
            else:
                dets.append({"cls": "pothole", "conf": round(float(p), 3), "box": box, "sources": ["pothole detector"]})

    def _full_frame_potholes(self, frame, gray, dets, view) -> list[tuple[np.ndarray, list, float]]:
        """Pothole proposals from the segmenter that the detector did not already find.

        On street-level frames the segmenter hallucinates potholes on trees, sky, vehicles and
        crosswalks, so proposals must be confident, low-saturation (grey/brown pavement), low in the
        frame and not painted markings. Close-up photos get looser limits.
        """
        h, w = gray.shape
        street = view == "dashcam"
        min_conf, max_sat, min_cy, max_area = (0.6, 55, 0.45, 0.08) if street else (0.3, 70, 0.2, 0.6)
        with self.lock:
            r = self.seg.predict(frame, conf=min_conf, imgsz=DEFAULTS["imgsz"], device=self.device,
                                 verbose=False, retina_masks=True)[0]
        if r.masks is None or len(r.masks) == 0:
            return []
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        sat = hsv[..., 1]
        # grass, foliage and roadside flowers: yellow-green to green hues with strong colour
        vegetation = (hsv[..., 0] >= 22) & (hsv[..., 0] <= 90) & (sat > 70)
        existing = [d["box"] for d in dets if d["cls"] == "pothole"]
        out = []
        for m, c in zip(r.masks.data.cpu().numpy(), r.boxes.conf.cpu().numpy()):
            m = (m > 0.5).astype(np.uint8)
            if m.shape != gray.shape:
                m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
            ys, xs = np.nonzero(m)
            if len(ys) < 50:
                continue
            box = [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())]
            if (m.mean() > max_area or ys.mean() / h < min_cy or float(np.median(sat[m > 0])) > max_sat
                    or float(vegetation[m > 0].mean()) > 0.15 or _is_road_marking(gray, box)):
                continue
            if street and (box[0] <= 0.01 * w or box[2] >= 0.99 * w):
                continue  # in street views, regions hugging the side edges are verges / shoulders / shadows
            if any(_overlap(box, e) > 0.4 for e in existing + [o[1] for o in out]):
                continue  # already found by the detector (refined via crop segmentation) or a duplicate
            out.append((m, [round(v, 1) for v in box], float(c)))
        return out

    @staticmethod
    def _damaged_sections(dets, w, h) -> list[dict]:
        """Union-find clustering of defects whose (slightly expanded) boxes touch."""
        n = len(dets)
        if n < 2:
            return []
        gap = 0.04 * max(w, h)
        parent = list(range(n))

        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a

        boxes = np.array([d["box"] for d in dets])
        for i in range(n):
            for j in range(i + 1, n):
                a, b = boxes[i], boxes[j]
                if a[0] - gap <= b[2] and b[0] - gap <= a[2] and a[1] - gap <= b[3] and b[1] - gap <= a[3]:
                    parent[find(i)] = find(j)
        groups: dict[int, list[int]] = {}
        for i in range(n):
            groups.setdefault(find(i), []).append(i)

        sections = []
        for members in groups.values():
            if len(members) < 2:
                continue
            kinds = {dets[m]["cls"] for m in members}
            bb = boxes[members]
            x1, y1, x2, y2 = bb[:, 0].min(), bb[:, 1].min(), bb[:, 2].max(), bb[:, 3].max()
            area_frac = (x2 - x1) * (y2 - y1) / (w * h)
            if len(members) >= 3 or (len(kinds) >= 2 and area_frac >= 0.06):
                sections.append({
                    "cls": "damaged_section",
                    "conf": round(float(np.mean([dets[m]["conf"] for m in members])), 3),
                    "box": [round(float(v), 1) for v in (x1, y1, x2, y2)],
                    "members": len(members),
                    "member_types": sorted(kinds),
                    "member_idx": members,
                })
        return sections


def _overlap(a, b) -> float:
    """Intersection over the smaller box's area."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / small if small > 0 else 0.0


def _is_road_marking(gray: np.ndarray, box) -> bool:
    """Crosswalks / painted symbols: a large share of bright paint pixels inside the box."""
    x1, y1, x2, y2 = [int(v) for v in box]
    crop = gray[max(0, y1):y2, max(0, x1):x2]
    if crop.size == 0:
        return False
    thr = max(170.0, 1.35 * float(np.median(gray[gray.shape[0] // 2:])))
    return float((crop > thr).mean()) > 0.18


def _mask_to_polygon(mask: np.ndarray) -> list[list[float]]:
    cnts, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return []
    c = max(cnts, key=cv2.contourArea)
    c = cv2.approxPolyDP(c, 0.004 * cv2.arcLength(c, True), True)
    return [[float(p[0][0]), float(p[0][1])] for p in c]


# ---------------------------------------------------------------- rendering

def render(frame: np.ndarray, dets: list[dict], color_by: str = "severity") -> np.ndarray:
    """Draw detections (boxes, pothole masks, labels) onto a copy of the frame."""
    out = frame.copy()
    h, w = out.shape[:2]
    th = max(2, int(round(min(w, h) / 320)))
    fs = max(0.45, min(w, h) / 1100)
    overlay = out.copy()
    for d in dets:
        if d.get("polygon"):
            col = _hex_to_bgr(CLASSES["pothole"]["color"])
            cv2.fillPoly(overlay, [np.array(d["polygon"], np.int32)], col)
    cv2.addWeighted(overlay, 0.35, out, 0.65, 0, out)

    for d in sorted(dets, key=lambda d: d["cls"] != "damaged_section"):  # sections underneath
        color = _hex_to_bgr(SEVERITY_LEVELS[d["severity"]]["color"] if color_by == "severity"
                            else CLASSES[d["cls"]]["color"])
        x1, y1, x2, y2 = [int(v) for v in d["box"]]
        if d["cls"] == "damaged_section":
            _dashed_rect(out, (x1, y1), (x2, y2), color, th)
        else:
            cv2.rectangle(out, (x1, y1), (x2, y2), color, th, cv2.LINE_AA)
        label = f"{CLASSES[d['cls']]['label']} | {d['severity']} {d['score']:.0f}"
        (tw, tht), bl = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, fs, max(1, th - 1))
        if d["cls"] == "damaged_section":  # bottom edge, so it doesn't collide with member labels
            ty = y2 - bl - 2
        else:
            ty = y1 - 4 if y1 - tht - 8 > 0 else y1 + tht + 6
        cv2.rectangle(out, (x1, ty - tht - 4), (x1 + tw + 6, ty + bl - 2), color, -1)
        cv2.putText(out, label, (x1 + 3, ty - 2), cv2.FONT_HERSHEY_SIMPLEX, fs, (255, 255, 255),
                    max(1, th - 1), cv2.LINE_AA)
    return out


def _dashed_rect(img, p1, p2, color, th, dash=14):
    x1, y1 = p1
    x2, y2 = p2
    for x in range(x1, x2, dash * 2):
        cv2.line(img, (x, y1), (min(x + dash, x2), y1), color, th)
        cv2.line(img, (x, y2), (min(x + dash, x2), y2), color, th)
    for y in range(y1, y2, dash * 2):
        cv2.line(img, (x1, y), (x1, min(y + dash, y2)), color, th)
        cv2.line(img, (x2, y), (x2, min(y + dash, y2)), color, th)
