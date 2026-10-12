"""Central configuration for the Road Damage Detection system."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OUTPUT_DIR = DATA_DIR / "outputs"
DB_PATH = DATA_DIR / "road_damage.db"

for d in (UPLOAD_DIR, OUTPUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Pretrained weights (downloaded from the Hugging Face Hub on first run if missing)
DETECTOR = {
    "path": MODELS_DIR / "rdd2022_yolov8s.pt",
    "repo": "dronefreak/rdd2022-yolov8s",
    "file": "best.pt",
}
# General-purpose pothole detector (trained on varied pothole photos incl. close-ups / web images)
POTHOLE_DETECTOR = {
    "path": MODELS_DIR / "pothole_det_yolov8.pt",
    "repo": "Harisanth/Pothole-Finetuned-YOLOv8",
    "file": "best.pt",
}
SEGMENTER = {
    "path": MODELS_DIR / "pothole_seg_yolov8s.pt",
    "repo": "keremberke/yolov8s-pothole-segmentation",
    "file": "best.pt",
}

# Damage taxonomy. RDD2022 codes: D00 longitudinal, D10 transverse, D20 alligator, D40 pothole.
# "damaged_section" is derived by clustering co-located detections (see detector.py).
CLASSES = {
    "longitudinal_crack": {"label": "Longitudinal crack", "code": "D00", "color": "#2a78d6", "weight": 0.50},
    "transverse_crack":   {"label": "Transverse crack",   "code": "D10", "color": "#eb6834", "weight": 0.55},
    "alligator_crack":    {"label": "Alligator crack",    "code": "D20", "color": "#1baf7a", "weight": 0.80},
    "pothole":            {"label": "Pothole",            "code": "D40", "color": "#4a3aa7", "weight": 1.00},
    "damaged_section":    {"label": "Damaged section",    "code": "DS",  "color": "#e87ba4", "weight": 0.90},
}

SEVERITY_LEVELS = {
    "Critical": {"color": "#d03b3b", "min_score": 60},
    "Moderate": {"color": "#fab219", "min_score": 30},
    "Minor":    {"color": "#0ca30c", "min_score": 0},
}

DEFAULTS = {
    "conf": 0.25,           # detector confidence threshold
    "iou": 0.5,             # NMS IoU
    "imgsz": 640,
    "video_fps": 4.0,       # frames sampled per second of video
    "max_video_frames": 1200,
    "view": "auto",         # "auto" (per image), "dashcam" (street-level, perspective) or "topdown" (close-up / drone)
}
