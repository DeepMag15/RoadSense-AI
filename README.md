# RoadSense AI — Road Damage Detection & Severity Analysis

RoadSense AI finds road damage in photos and videos and turns it into a repair plan. Upload road images or dashcam / CCTV video. Deep-learning models detect **potholes, longitudinal cracks, transverse cracks, alligator cracks and damaged road sections**. The system scores each defect's **severity**, rates each road with a **Road Condition Index (RCI)** and a **repair priority**, and shows everything in a web dashboard with PDF and CSV reports.

Intended users: municipalities and road-maintenance teams who want to triage which roads need repair first, without inspecting every road by hand.

```
Road photo / dashcam / CCTV video
        ↓  frame extraction (videos: 1–10 frames per second)
        ↓  photo-type check: street-level view or close-up photo
        ↓  detection: RDD2022 road-damage model + general pothole model
        ↓  segmentation: outline and verify every pothole
        ↓  false-alarm filters + grouping into "damaged sections"
        ↓  tracking across video frames (each defect counted once)
        ↓  severity score 0–100 → Minor / Moderate / Critical
        ↓  Road Condition Index + repair priority + recommended repair
        ↓  dashboard · map · interactive viewer · PDF report · CSV export
```

---

## Contents

1. [Quick start](#quick-start)
2. [Using the app](#using-the-app)
3. [How it works](#how-it-works)
4. [Severity and Road Condition Index](#severity-and-road-condition-index)
5. [Pretrained models](#pretrained-models)
6. [Project layout](#project-layout)
7. [REST API](#rest-api)
8. [Configuration](#configuration)
9. [Troubleshooting](#troubleshooting)
10. [Limitations](#limitations)

---

## Quick start

**Requirements:** Python 3.10+ (developed on 3.12), about 2 GB of free disk space, and Windows, Linux or macOS. A GPU is optional.

```bash
pip install -r requirements.txt
python run.py                      # then open http://127.0.0.1:8000
```

On Windows you can instead double-click **`start.bat`**. It starts the server in its own window and opens the browser; close that window to stop the server.

`run.py` accepts `--host` and `--port`. For example, `python run.py --port 8080` serves on another port, and `--host 0.0.0.0` makes the app reachable from other machines on your network.

To try it without your own data: **New inspection → "Select all" sample images → Run analysis**.

The three pretrained models ship in `models/`. If a file is missing, it is downloaded from the Hugging Face Hub on first start.

---

## Using the app

The sidebar has three sections.

### Dashboard

A network-wide overview of all completed inspections:

* **Tiles:**
  * roads inspected
  * defects detected
  * potholes detected
  * Critical, Moderate and Minor counts. A toggle switches these between all damage and potholes only.
  * average Road Condition Index
* **Charts:**
  * defects by type
  * severity by damage type
  * road-condition ratings
  * severity score distribution
  * inspection activity per day
* **Inspection map:** inspections with GPS coordinates, coloured by condition. Map tiles need an internet connection.
* **Repair priority table:** the worst roads first. Click a row to open that inspection.
* **Export all detections** downloads every detection as CSV.

### New inspection

1. **Media.** Drag in or browse for road photos (several at once) or **one** video. Supported formats:
   * **Images:** JPG, PNG, WEBP, BMP, TIFF
   * **Videos:** MP4, AVI, MOV, MKV, WEBM, M4V, MPG

   You can also pick from the built-in sample images.
2. **Road details (optional).** Inspection name, road / segment, area / ward, and location:
   * Set the location by clicking the map, using **use my current location**, or typing coordinates.
   * If you leave it empty, GPS is read from the photos' EXIF data when available.
3. **Analysis settings.**
   * **Camera view:**
     * **Auto-detect** (recommended) decides per image whether it is a street-level view or a close-up photo.
     * **Dashcam / street** forces street-level rules.
     * **Close-up / drone** forces close-up rules.
   * **Detection confidence threshold** (default 0.25). Lower finds more damage but gives more false alarms.
   * **Video frame sampling** (default 4 fps). Higher is more thorough and slower.

Click **Run analysis**. A progress bar shows each stage; you can leave the page while it runs.

### Inspections

All inspections appear as cards with a thumbnail, RCI and severity counts. You can search by name, road or area, and filter by *has critical*, *RCI < 55* or *video*.

Opening an inspection shows:

* **Summary:** Road Condition Index, rating, repair priority and severity counts.
* **Video inspections only:**
  * the annotated video
  * a **damage along the route** chart. Click a bar to jump to that moment.
* **Interactive viewer** for the analysed frames:
  * Colour boxes by severity or by damage type.
  * Toggle labels, or switch to the original image.
  * Filter by damage type or severity.
  * Use the arrow keys to move between frames.
  * Hover or click a defect to see its details: confidence, area or length, depth index, which models detected it, photo type, and the recommended repair.
* **Charts and a detections table.** Click a table row to jump to that defect in the viewer.
* **Action buttons:**
  * **PDF report** downloads the report for this inspection.
  * **CSV** downloads this inspection's detections.
  * **Re-analyse** runs the current models again on the original uploads. Use it after a model or rule update.
  * **Delete** removes the inspection and its files.

### PDF report

Each report contains:

* a summary: RCI, severity counts and repair priority
* inspection details
* charts: defects by type and by severity, plus damage along the route for videos
* a type × severity table
* a repair work list, most severe first, with a recommended action for each defect
* annotated evidence images
* a methodology note

---

## How it works

### 1. Frame extraction (`app/pipeline.py`)

* **Images:** each image is read, including WEBP and non-ASCII file paths. Images larger than 1920 px on their longest side are downscaled.
* **Videos:** frames are sampled at the chosen rate, up to 1,200 frames per video. A simple tracker matches each defect across consecutive sampled frames by overlap and distance, so a pothole seen in 10 frames counts **once**:
  * A defect is confirmed if it appears in at least 2 sampled frames, or once with confidence ≥ 0.5.
  * The best view of each confirmed defect is kept as a key frame.
  * The annotated video is re-encoded to H.264 with ffmpeg so it plays in the browser.

### 2. Photo-type check (`RoadDamageDetector.scene_type`)

Two kinds of photo need different rules:

* **Street-level views** (dashcam, CCTV, street photos) show the horizon. Potholes look small and distant, and there are trees, sky, vehicles and road markings that a model can mistake for potholes.
* **Close-up photos** (handheld, low-angle, drone, web images) show mostly pavement, and potholes are large in the frame.

The check looks at the top 30% of the image. It is called a street-level view when:

* at least 20% of it is **saturated blue sky**, or
* at least 30% of it is **bright grey overcast sky** that is also clearly brighter than the road at the bottom of the image.

Everything else is treated as a close-up. In testing, this classified 42 of 43 images correctly.

### 3. Detection (`app/detector.py`)

Three pretrained models work together. Each covers a weakness of another.

| Stage | Model | Role |
|---|---|---|
| A | **RDD2022 road-damage detector** | Finds all four damage types (D00 longitudinal, D10 transverse, D20 alligator, D40 pothole). Strong on dashcam and street views, but it **misses most potholes in close-up photos**: it only learned dashcam viewpoints |
| B | **General pothole detector** | Finds potholes in close-up, handheld and web photos. Its boxes are merged with stage A: where both models find the same pothole, it counts as one pothole with stronger verification |
| C | **Pothole segmenter** | Outlines each pothole found by A or B, using a crop around it. The outline gives the pothole's area and depth estimate and acts as a second opinion. It also proposes potholes on the full frame that A and B missed |

**False-alarm filters.** On street-level views, the models sometimes mistake road markings, manhole covers, shadows, trees, sky or the road shoulder for potholes. So:

* **Above the horizon:** detections ending above the horizon (top 36% of a street view) are dropped.
* **Road paint:** pothole boxes that are mostly bright paint (crosswalks, painted symbols) are dropped.
* **Large flat boxes:** on street views, large pothole boxes are dropped if they are no darker than the pavement around them, or if the segmenter cannot confirm them.
* **Segmenter-only potholes** (found by C but not A or B) must pass every check below:

| Check | Street view | Close-up |
|---|---|---|
| minimum confidence | 0.60 | 0.30 |
| colour | grey or brown pavement (saturation ≤ 55) | saturation ≤ 70 |
| vegetation | < 15% of the outline | < 15% of the outline |
| position | lower part of the frame | not at the very top |
| maximum size | 8% of the frame | 60% of the frame |
| other | not touching the left or right edge; not road paint | not road paint |

* **Pothole detector (B):** needs confidence ≥ 0.50 on street views and ≥ 0.30 on close-ups.

**Damaged sections.** Defects whose boxes touch or nearly touch are grouped. A group becomes a **damaged section** when it has at least 3 defects, or at least 2 different damage types covering at least 6% of the frame.

---

## Severity and Road Condition Index

All formulas are in `app/severity.py`.

### Defect severity (0–100)

```
score = 100 · min(1, base · (0.85 + 0.15 · confidence) + aux)
```

`sat(x, k) = 1 − e^(−x/k)` is a saturating curve, so very large defects don't run off the scale. `s` is the **perspective factor**: on street views, defects nearer the horizon are farther away and look smaller, so their size is scaled up (1 to 4×). On close-ups, `s = 1`.

| Damage type | Weight | `base` | `aux` (extra cue) |
|---|---|---|---|
| Pothole | 1.00 | `1.0 · (0.2 + 0.6·sat(area·s², 0.03)) · verify` | `0.2 · depth index` |
| Longitudinal crack | 0.50 | `0.50 · (0.25 + 0.75·sat(length·s, 0.35))` | `0.2 · sat(width index, 1.0)` |
| Transverse crack | 0.55 | `0.55 · (0.25 + 0.75·sat(length·s, 0.35))` | `0.2 · sat(width index, 1.0)` |
| Alligator crack | 0.80 | `0.80 · (0.25 + 0.75·sat(area·s², 0.10))` | `0.1 · crack density` |
| Damaged section | — | worst member's score + 4 per additional defect | — |

* **area** is a fraction of the frame. For potholes it uses the segmentation outline when available; otherwise it is 65% of the box.
* **length** is the box diagonal as a fraction of the frame diagonal.
* **depth index** (0–1) is how much darker the pothole's inside is than the surrounding pavement.
* **width index** and **crack density** come from dark-pixel analysis inside the box.
* **verify** = `0.6 + 0.4 · support`. `support` is the segmenter's confidence, or 0.8 when two detectors agree, whichever is higher; with neither, it is 0.25.

| Severity | Score | Shown as |
|---|---|---|
| **Critical** | ≥ 60 | red |
| **Moderate** | 30–59 | amber |
| **Minor** | < 30 | green |

Every defect also gets a **recommended repair** based on its type and severity, for example *"Barricade and apply full-depth patch immediately"* for a Critical pothole, or *"Rout and seal crack"* for a Moderate crack.

### Road Condition Index (RCI, 0–100)

A PCI-style index for the whole inspection, where 100 means a perfect road:

```
deduct = Σ 40 · (score / 100)^1.3   ÷  views
RCI    = 100 · e^(−deduct / 60)
```

* **views** is the number of images. For a video it is duration ÷ 5 s, roughly one view of road per 5 seconds of driving.
* Damaged sections are left out of the sum because their member defects are already counted.

| RCI | Rating |
|---|---|
| ≥ 85 | Good |
| 70–84 | Satisfactory |
| 55–69 | Fair |
| 40–54 | Poor |
| 25–39 | Very Poor |
| 10–24 | Serious |
| < 10 | Failed |

**Repair priority:**

| Priority | When |
|---|---|
| **Immediate (within 7 days)** | any Critical defect, or RCI < 40 |
| **Short-term (within 30 days)** | any Moderate defect, or RCI < 70 |
| **Routine (next maintenance cycle)** | only Minor defects |
| **No action required** | no defects |

> These scores are heuristics for **prioritising field inspection**. They are not engineering measurements. Weights and thresholds can be tuned in `app/config.py` and `app/severity.py`.

---

## Pretrained models

| File in `models/` | Hugging Face model | Details |
|---|---|---|
| `rdd2022_yolov8s.pt` | [`dronefreak/rdd2022-yolov8s`](https://huggingface.co/dronefreak/rdd2022-yolov8s) | YOLOv8s fine-tuned on [RDD2022](https://github.com/sekilab/RoadDamageDetector), about 38,000 road images from Japan, India, the Czech Republic, Norway, the USA and China. Test mAP@50 = 61.5%. License: AGPL-3.0 |
| `pothole_det_yolov8.pt` | [`Harisanth/Pothole-Finetuned-YOLOv8`](https://huggingface.co/Harisanth/Pothole-Finetuned-YOLOv8) | YOLOv8 single-class pothole detector trained on varied pothole photos. License: MIT |
| `pothole_seg_yolov8s.pt` | [`keremberke/yolov8s-pothole-segmentation`](https://huggingface.co/keremberke/yolov8s-pothole-segmentation) | YOLOv8s-seg pothole segmentation model |

**Why the general pothole detector was chosen.** Seven candidate pothole models from Hugging Face were benchmarked. Each was scored on finding potholes in 11 close-up pothole photos, and on how many of the 32 street-view sample images it wrongly fired on. This model found potholes in **11 of 11** photos and had the fewest street-view hits (8 of 32, several of them real potholes).

**Measured effect of the three-model setup** (small internal test sets):

| Setup | Close-up photos with the pothole found |
|---|---|
| RDD2022 detector only | 0 / 10 |
| + segmenter proposals | 7 / 10 |
| + general pothole detector | **10 / 10** |

On street-view images, results stayed essentially the same. The pothole detector adds a few low-score (Minor) hits, mostly on manhole covers and patches.

**Speed:** about 0.45 s per image on CPU. A CUDA GPU is used automatically if your PyTorch build supports it.

---

## Project layout

```
DL_project/
├── run.py              start the server (python run.py [--host] [--port])
├── start.bat           Windows launcher: starts the server and opens the browser
├── requirements.txt
├── app/
│   ├── config.py       paths, model sources, damage classes, colours, thresholds, defaults
│   ├── detector.py     three-model inference, photo-type check, false-alarm filters,
│   │                   damaged-section grouping, drawing annotated images
│   ├── severity.py     defect scores, depth / crack measurements, RCI, priority, repairs
│   ├── pipeline.py     background jobs: image batches, video sampling, tracking,
│   │                   H.264 encoding, EXIF GPS, summaries
│   ├── report.py       PDF report (reportlab + matplotlib)
│   ├── db.py           SQLite storage
│   ├── main.py         FastAPI app: REST API + serves the front-end
│   └── static/         single-page front-end (HTML/CSS/vanilla JS)
│       └── vendor/     Chart.js and Leaflet, bundled so the UI works offline
├── models/             the three pretrained model files
├── samples/            32 RDD2022 test images (7 countries) + synthetic_drive_demo.mp4
└── data/               created at runtime
    ├── road_damage.db  inspections, frames, detections
    ├── uploads/        original uploaded files (used by Re-analyse)
    └── outputs/        analysed frames, annotated images, crops, annotated videos
```

`samples/synthetic_drive_demo.mp4` is a zoom-through clip built from sample images. It is for testing the video pipeline, not real dashcam footage.

---

## REST API

Interactive API docs: **http://127.0.0.1:8000/docs**

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/config` | damage classes, severity levels, defaults, loaded models, sample list |
| `POST` | `/api/inspections` | create an inspection (multipart form, see below); returns `{"id": ...}` |
| `GET` | `/api/inspections` | list all inspections with summaries |
| `GET` | `/api/inspections/{id}` | full inspection: frames, detections, summary, video URL |
| `GET` | `/api/inspections/{id}/status` | progress while analysing: `queued`, `processing`, `done` or `failed` |
| `POST` | `/api/inspections/{id}/rerun` | analyse the original uploads again with the current models |
| `DELETE` | `/api/inspections/{id}` | delete an inspection and its files |
| `GET` | `/api/inspections/{id}/report.pdf` | download the PDF report |
| `GET` | `/api/export.csv?inspection_id={id}` | detections as CSV; omit `inspection_id` for all inspections |
| `GET` | `/api/stats` | dashboard numbers: counts, severity matrix, ratings, map points, worst roads |

**`POST /api/inspections` form fields:**

| Field | Required | Default | Meaning |
|---|---|---|---|
| `files` | one of `files` / `samples` | — | images, or one video (repeat `files` for multiple images) |
| `samples` | one of `files` / `samples` | — | comma-separated sample image names from `samples/` |
| `name`, `road_name`, `location` | no | auto | descriptive labels |
| `lat`, `lon` | no | from EXIF | GPS coordinates |
| `conf` | no | `0.25` | detection confidence threshold (0.05–0.95) |
| `view` | no | `auto` | `auto`, `dashcam` (street-level) or `topdown` (close-up / drone) |
| `video_fps` | no | `4` | frames analysed per second of video (0.5–15) |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/inspections \
     -F "files=@road1.jpg" -F "files=@road2.jpg" \
     -F "road_name=MG Road" -F "location=Ward 12"
```

**CSV columns:** inspection ID and name, road name, location, latitude, longitude, source file, video time, damage type and code, severity, score, confidence, box coordinates, and recommended repair.

---

## Configuration

These defaults live in `app/config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `DEFAULTS["conf"]` | 0.25 | RDD2022 detector confidence threshold (overridable per inspection) |
| `DEFAULTS["view"]` | `auto` | camera view mode |
| `DEFAULTS["video_fps"]` | 4 | video sampling rate |
| `DEFAULTS["max_video_frames"]` | 1200 | maximum frames analysed per video |
| `DEFAULTS["imgsz"]` | 640 | model input size |
| `CLASSES[...]["weight"]` | see table above | damage-type weight in the severity score |
| `SEVERITY_LEVELS[...]["min_score"]` | 60 / 30 / 0 | Critical / Moderate / Minor cut-offs |

The false-alarm thresholds are in `app/detector.py`, and the severity curves, RCI bands and repair recommendations are in `app/severity.py`.

After changing models or rules, **restart the server**, then use **Re-analyse** on any existing inspections you want updated.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| Code changes don't take effect | Restart the server: close the `start.bat` window or press Ctrl+C, then start it again. Refresh the browser with Ctrl+F5 |
| An old inspection still shows old results | Open it and click **Re-analyse** |
| `address already in use` / port 8000 busy | Another server is already running on 8000. Close it, or run `python run.py --port 8080` and open that port |
| A photo with an obvious pothole shows no damage | Set **Camera view** to *Close-up / drone* and lower the confidence threshold, then Re-analyse. Very blurry or shadowed potholes can still be missed |
| Too many false alarms on street footage | Set **Camera view** to *Dashcam / street* and raise the confidence threshold |
| Map is blank | The map tiles come from OpenStreetMap and need an internet connection. Everything else works offline |
| Video won't play in the browser | Install ffmpeg or keep `imageio-ffmpeg` installed. Without it, the annotated video can't be re-encoded to browser-playable H.264 |
| Processing is slow | Lower the video sampling rate, or install a CUDA build of PyTorch: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121` |
| Model files are missing and won't download | Download them from the Hugging Face links in [Pretrained models](#pretrained-models) and save them in `models/` with the file names shown |
| An inspection says "Interrupted by server restart" | The server was stopped mid-analysis. Click **Re-analyse** |

---

## Limitations

* **Heuristic severity.** Scores and the RCI are relative estimates for prioritising inspections, not engineering measurements.
* **Relative sizes.** Areas and lengths are percentages of the frame, not metres. Metric sizes would need camera calibration.
* **Manhole covers and patches.** The RDD2022 training labels count manholes and markings as potholes, so the detectors lean that way. The filters remove most, but some low-score false potholes remain on street views.
* **Hard cases.** Very faint, blurry, shadowed or water-filled potholes can be missed by all three models.
* **Small test sets.** The accuracy figures above come from small internal test sets (11 close-up and 32 street-view images). Before relying on the system in production, evaluate it on photos from your own roads and cameras.
* **One video per inspection.** Videos can't be mixed with images in the same inspection.
* **Model licenses.** The RDD2022 detector is AGPL-3.0. Check its terms before commercial use.
