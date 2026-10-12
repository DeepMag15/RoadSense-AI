"""Inspection jobs: image batches and videos -> detections, key frames, summary statistics."""
from __future__ import annotations

import subprocess
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import ExifTags, Image

from . import db
from . import severity as sev
from .config import CLASSES, DEFAULTS, OUTPUT_DIR, ROOT, SEVERITY_LEVELS
from .detector import RoadDamageDetector, render

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg"}
MAX_SIDE = 1920  # frames are stored/analysed at most this large

_executor = ThreadPoolExecutor(max_workers=1)  # one job at a time keeps CPU inference responsive
_detector: RoadDamageDetector | None = None


def detector() -> RoadDamageDetector:
    global _detector
    if _detector is None:
        _detector = RoadDamageDetector()
    return _detector


def submit(insp_id: str, files: list[Path], settings: dict):
    _executor.submit(_run, insp_id, files, settings)


def rel(p: Path) -> str:
    """Path relative to the data dir, used to build /media URLs."""
    return p.relative_to(OUTPUT_DIR).as_posix()


# --------------------------------------------------------------------------- helpers

def _read_image(path: Path) -> np.ndarray | None:
    data = np.fromfile(str(path), dtype=np.uint8)  # handles non-ASCII Windows paths
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        try:
            img = cv2.cvtColor(np.array(Image.open(path).convert("RGB")), cv2.COLOR_RGB2BGR)
        except Exception:
            return None
    return _limit(img)


def _limit(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    s = MAX_SIDE / max(h, w)
    return cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA) if s < 1 else img


def _write(path: Path, img: np.ndarray, quality: int = 88):
    path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tofile(str(path))


def exif_gps(path: Path) -> tuple[float, float] | None:
    try:
        exif = Image.open(path).getexif()
        gps = exif.get_ifd(0x8825)
        if not gps:
            return None
        g = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}

        def dms(v):
            return float(v[0]) + float(v[1]) / 60 + float(v[2]) / 3600

        lat, lon = dms(g["GPSLatitude"]), dms(g["GPSLongitude"])
        if g.get("GPSLatitudeRef") == "S":
            lat = -lat
        if g.get("GPSLongitudeRef") == "W":
            lon = -lon
        return round(lat, 6), round(lon, 6)
    except Exception:
        return None


def _crop(img, box, out: Path):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = box
    px, py = 0.2 * (x2 - x1) + 8, 0.2 * (y2 - y1) + 8
    c = img[int(max(0, y1 - py)):int(min(h, y2 + py)), int(max(0, x1 - px)):int(min(w, x2 + px))]
    if c.size == 0:
        return None
    s = 320 / max(c.shape[:2])
    if s < 1:
        c = cv2.resize(c, (int(c.shape[1] * s), int(c.shape[0] * s)), interpolation=cv2.INTER_AREA)
    _write(out, c, 85)
    return rel(out)


def _save_frame(insp_id, out_dir: Path, idx, img, dets, source_name, t_sec=None, gps=None, track_ids=None):
    raw_p, ann_p = out_dir / f"frame_{idx:05d}.jpg", out_dir / f"frame_{idx:05d}_ann.jpg"
    _write(raw_p, img)
    _write(ann_p, render(img, dets))
    h, w = img.shape[:2]
    frame_id = db.insert("frames", inspection_id=insp_id, idx=idx, source_name=source_name, t_sec=t_sec,
                         width=w, height=h, image_path=rel(raw_p), annotated_path=rel(ann_p),
                         lat=gps[0] if gps else None, lon=gps[1] if gps else None)
    for j, d in enumerate(dets):
        crop = _crop(img, d["box"], out_dir / "crops" / f"{idx:05d}_{j}.jpg")
        db.insert("detections", inspection_id=insp_id, frame_id=frame_id,
                  track_id=(track_ids[j] if track_ids else None), cls=d["cls"], conf=d["conf"],
                  score=d["score"], severity=d["severity"], x1=d["box"][0], y1=d["box"][1],
                  x2=d["box"][2], y2=d["box"][3], polygon=d.get("polygon"), metrics=d.get("metrics"),
                  recommendation=d["recommendation"], crop_path=crop, t_sec=t_sec, hits=d.get("hits", 1))
    return frame_id


# --------------------------------------------------------------------------- jobs

def _run(insp_id: str, files: list[Path], settings: dict):
    try:
        db.update("inspections", insp_id, status="processing", progress=0.01, message="Loading models")
        det = detector()
        out_dir = OUTPUT_DIR / insp_id
        out_dir.mkdir(parents=True, exist_ok=True)
        videos = [f for f in files if f.suffix.lower() in VIDEO_EXT]
        if videos:
            stats = _process_video(insp_id, videos[0], out_dir, settings, det)
        else:
            stats = _process_images(insp_id, files, out_dir, settings, det)
        summary = build_summary(insp_id, **stats)
        db.update("inspections", insp_id, status="done", progress=1.0, message="Complete", summary=summary,
                  finished_at=datetime.now().isoformat(timespec="seconds"))
    except Exception as e:  # surface the error in the UI instead of dying silently
        traceback.print_exc()
        db.update("inspections", insp_id, status="failed", message=f"{type(e).__name__}: {e}")


def _process_images(insp_id, files, out_dir, settings, det):
    n = len(files)
    insp = db.one("SELECT lat, lon FROM inspections WHERE id=?", (insp_id,))
    for i, f in enumerate(files):
        db.update("inspections", insp_id, progress=0.02 + 0.95 * i / n, message=f"Analysing image {i + 1}/{n}")
        img = _read_image(f)
        if img is None:
            continue
        gps = exif_gps(f)
        if gps and insp["lat"] is None:
            db.update("inspections", insp_id, lat=gps[0], lon=gps[1])
            insp = {"lat": gps[0], "lon": gps[1]}
        dets = det.analyze(img, conf=settings["conf"], view=settings["view"])
        _save_frame(insp_id, out_dir, i, img, dets, f.name.split("__", 1)[-1], gps=gps)
    return {"effective_frames": max(n, 1), "timeline": None}


class IoUTracker:
    """Greedy IoU / centre-distance tracker so a pothole seen in 10 consecutive frames counts once."""

    def __init__(self, max_gap=3):
        self.tracks: dict[int, dict] = {}
        self.next_id = 1
        self.max_gap = max_gap

    @staticmethod
    def _iou(a, b):
        ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
        iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
        inter = ix * iy
        u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return inter / u if u > 0 else 0.0

    def update(self, dets, fidx, diag) -> list[int]:
        ids = []
        live = {k: t for k, t in self.tracks.items() if fidx - t["last"] <= self.max_gap}
        used = set()
        for d in dets:
            best, best_s = None, 0.0
            cx, cy = (d["box"][0] + d["box"][2]) / 2, (d["box"][1] + d["box"][3]) / 2
            for k, t in live.items():
                if k in used or t["cls"] != d["cls"]:
                    continue
                tb = t["box"]
                tcx, tcy = (tb[0] + tb[2]) / 2, (tb[1] + tb[3]) / 2
                dist = ((cx - tcx) ** 2 + (cy - tcy) ** 2) ** 0.5 / diag
                s = max(self._iou(d["box"], tb), 0.3 * (1 - dist / 0.12) if dist < 0.12 else 0.0)
                if s > best_s and s > 0.1:
                    best, best_s = k, s
            if best is None:
                best = self.next_id
                self.next_id += 1
                self.tracks[best] = {"cls": d["cls"], "hits": 0, "best": None, "best_frame": None}
            t = self.tracks[best]
            t.update(box=d["box"], last=fidx)
            t["hits"] += 1
            if t["best"] is None or d["score"] > t["best"]["score"]:
                t["best"], t["best_frame"] = d, fidx
            used.add(best)
            ids.append(best)
        return ids


def _process_video(insp_id, path: Path, out_dir, settings, det):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError("Could not open video (unsupported codec?)")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    duration = total / fps if total else 0
    step = max(1, int(round(fps / settings["video_fps"])))
    expected = min(total // step + 1 if total else DEFAULTS["max_video_frames"], DEFAULTS["max_video_frames"])
    out_fps = fps / step

    tracker = IoUTracker()
    writer, tmp_video = None, out_dir / "annotated_raw.mp4"
    frame_store: dict[int, tuple[np.ndarray, list, list]] = {}  # sampled idx -> (frame, dets, track ids)
    timeline = []
    fidx, sampled = 0, 0
    while sampled < DEFAULTS["max_video_frames"]:
        ok = cap.grab()
        if not ok:
            break
        if fidx % step:
            fidx += 1
            continue
        ok, frame = cap.retrieve()
        if not ok:
            break
        frame = _limit(frame)
        h, w = frame.shape[:2]
        dets = det.analyze(frame, conf=settings["conf"], view=settings["view"])
        ids = tracker.update(dets, sampled, (w * w + h * h) ** 0.5)
        frame_store[sampled] = (frame, dets, ids)
        t = round(fidx / fps, 2)
        timeline.append({"t": t, "n": len(dets),
                         **{s: sum(1 for d in dets if d["severity"] == s) for s in SEVERITY_LEVELS}})
        ann = render(frame, dets)
        cv2.putText(ann, f"t={t:6.1f}s  defects={len(dets)}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)
        if writer is None:
            writer = cv2.VideoWriter(str(tmp_video), cv2.VideoWriter_fourcc(*"mp4v"), out_fps, (w, h))
        writer.write(ann)
        # keep only frames that are still the best view of some track (bounded memory)
        needed = {tr["best_frame"] for tr in tracker.tracks.values()}
        for k in [k for k in frame_store if k not in needed and k != sampled]:
            del frame_store[k]
        sampled += 1
        fidx += 1
        if sampled % 3 == 0:
            db.update("inspections", insp_id, progress=0.02 + 0.85 * min(sampled / max(expected, 1), 1.0),
                      message=f"Analysing frame {sampled}/{expected}")
    cap.release()
    if writer is not None:
        writer.release()

    db.update("inspections", insp_id, progress=0.9, message="Encoding annotated video")
    video_rel = _transcode(tmp_video, out_dir / "annotated.mp4")
    db.update("inspections", insp_id, video_path=video_rel)

    # a defect must be seen in >= 2 sampled frames (or once with high confidence) to count
    confirmed = {k: t for k, t in tracker.tracks.items()
                 if t["hits"] >= 2 or t["best"]["conf"] >= 0.5 or sampled <= 2}
    by_frame: dict[int, list[int]] = defaultdict(list)
    for k, t in confirmed.items():
        by_frame[t["best_frame"]].append(k)
    db.update("inspections", insp_id, progress=0.95, message="Saving key frames")
    for fi in sorted(by_frame):
        frame, _, _ = frame_store[fi]
        keys = by_frame[fi]
        dets = [dict(confirmed[k]["best"], hits=confirmed[k]["hits"]) for k in keys]
        _save_frame(insp_id, out_dir, fi, frame, dets, path.name.split("__", 1)[-1],
                    t_sec=timeline[fi]["t"], track_ids=keys)
    duration = duration or sampled / settings["video_fps"]
    # roughly one "view" of road per 5 s of driving, for RCI normalisation
    return {"effective_frames": max(duration / 5.0, 1.0), "timeline": timeline,
            "video": {"duration": round(duration, 1), "fps": round(fps, 1), "sampled_frames": sampled,
                      "tracks_total": len(tracker.tracks), "tracks_confirmed": len(confirmed)}}


def _transcode(src: Path, dst: Path) -> str | None:
    """OpenCV writes mp4v, which browsers can't play; re-encode to H.264 with ffmpeg."""
    if not src.exists():
        return None
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = "ffmpeg"
    try:
        subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(src), "-c:v", "libx264", "-preset", "veryfast",
                        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dst)], check=True, timeout=900)
        src.unlink(missing_ok=True)
        return rel(dst)
    except Exception:
        traceback.print_exc()
        return rel(src)


# --------------------------------------------------------------------------- summary

def build_summary(insp_id, effective_frames, timeline=None, video=None) -> dict:
    dets = db.query("SELECT cls, severity, score, conf FROM detections WHERE inspection_id=?", (insp_id,))
    frames = db.query("SELECT f.id, COUNT(d.id) n FROM frames f LEFT JOIN detections d ON d.frame_id=f.id "
                      "WHERE f.inspection_id=? GROUP BY f.id", (insp_id,))
    by_sev = Counter(d["severity"] for d in dets)
    by_cls = Counter(d["cls"] for d in dets)
    matrix = {c: {s: 0 for s in SEVERITY_LEVELS} for c in CLASSES}
    for d in dets:
        matrix[d["cls"]][d["severity"]] += 1
    # sections are aggregates of other defects; exclude them from the index to avoid double counting
    scores = [d["score"] for d in dets if d["cls"] != "damaged_section"]
    rci = sev.road_condition_index(scores, effective_frames)
    sev_counts = {s: by_sev.get(s, 0) for s in SEVERITY_LEVELS}
    return {
        "total": len(dets),
        "by_severity": sev_counts,
        "by_class": {c: by_cls.get(c, 0) for c in CLASSES},
        "matrix": matrix,
        "avg_score": round(float(np.mean(scores)), 1) if scores else 0.0,
        "max_score": round(float(max(scores)), 1) if scores else 0.0,
        "rci": rci,
        "rating": sev.rci_rating(rci),
        "priority": sev.repair_priority(sev_counts, rci),
        "frames_analyzed": len(frames) if timeline is None else len(timeline),
        "frames_with_damage": (sum(1 for f in frames if f["n"]) if timeline is None
                               else sum(1 for t in timeline if t["n"])),
        "timeline": timeline,
        "video": video,
    }


def sample_files() -> list[Path]:
    return sorted((ROOT / "samples").glob("*.jpg"))
