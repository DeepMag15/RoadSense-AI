"""FastAPI application: REST API + static single-page dashboard."""
from __future__ import annotations

import csv
import io
import shutil
import threading
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, pipeline
from .config import CLASSES, DEFAULTS, OUTPUT_DIR, ROOT, SEVERITY_LEVELS, UPLOAD_DIR
from .report import build_pdf

STATIC = Path(__file__).parent / "static"

app = FastAPI(title="RoadSense AI - Road Damage Detection", version="1.0")
app.mount("/media", StaticFiles(directory=OUTPUT_DIR), name="media")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
(ROOT / "samples").mkdir(exist_ok=True)
app.mount("/samples", StaticFiles(directory=ROOT / "samples"), name="samples")


@app.on_event("startup")
def _startup():
    db.init()
    # warm the models in the background so the first upload is fast
    threading.Thread(target=pipeline.detector, daemon=True).start()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    d = pipeline._detector
    return {"classes": CLASSES, "severity": SEVERITY_LEVELS, "defaults": DEFAULTS,
            "model": d.info() if d else {"status": "loading"},
            "samples": [p.name for p in pipeline.sample_files()]}


# ------------------------------------------------------------------ inspections

@app.post("/api/inspections")
async def create_inspection(
    files: list[UploadFile] = File(default=[]),
    samples: str = Form(""),
    name: str = Form(""), road_name: str = Form(""), location: str = Form(""),
    lat: str = Form(""), lon: str = Form(""),
    conf: float = Form(DEFAULTS["conf"]), view: str = Form(DEFAULTS["view"]),
    video_fps: float = Form(DEFAULTS["video_fps"]),
):
    insp_id = uuid.uuid4().hex[:10]
    up = UPLOAD_DIR / insp_id
    up.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for i, f in enumerate(files):
        ext = Path(f.filename or "").suffix.lower()
        if ext not in pipeline.IMAGE_EXT | pipeline.VIDEO_EXT:
            continue
        dst = up / f"{i:04d}__{Path(f.filename).name}"
        with dst.open("wb") as out:
            shutil.copyfileobj(f.file, out)
        saved.append(dst)
    sample_map = {p.name: p for p in pipeline.sample_files()}
    for i, s in enumerate(x for x in samples.split(",") if x.strip()):
        if s.strip() in sample_map:
            dst = up / f"s{i:03d}__{s.strip()}"
            shutil.copy(sample_map[s.strip()], dst)
            saved.append(dst)
    if not saved:
        raise HTTPException(400, "No supported image or video files were provided.")

    is_video = any(p.suffix.lower() in pipeline.VIDEO_EXT for p in saved)
    settings = {"conf": max(0.05, min(conf, 0.95)), "view": view if view in ("auto", "dashcam", "topdown") else "auto",
                "video_fps": max(0.5, min(video_fps, 15.0))}
    db.insert("inspections", id=insp_id,
              name=name.strip() or (f"Video - {saved[0].name.split('__', 1)[-1]}" if is_video
                                    else f"Image survey ({len(saved)} image{'s' if len(saved) > 1 else ''})"),
              road_name=road_name.strip(), location=location.strip(),
              lat=_f(lat), lon=_f(lon), media_type="video" if is_video else "image",
              status="queued", progress=0, message="Queued",
              created_at=datetime.now().isoformat(timespec="seconds"), settings=settings)
    pipeline.submit(insp_id, saved, settings)
    return {"id": insp_id}


def _f(v: str):
    try:
        return float(v) if str(v).strip() else None
    except ValueError:
        return None


@app.get("/api/inspections")
def list_inspections():
    rows = db.query("SELECT id, name, road_name, location, lat, lon, media_type, status, progress, message, "
                    "created_at, summary FROM inspections ORDER BY created_at DESC")
    for r in rows:
        thumb = db.one("SELECT annotated_path FROM frames f WHERE inspection_id=? ORDER BY "
                       "(SELECT MAX(score) FROM detections d WHERE d.frame_id=f.id) DESC LIMIT 1", (r["id"],))
        r["thumb"] = f"/media/{thumb['annotated_path']}" if thumb else None
        if r["summary"]:
            r["summary"].pop("timeline", None)
    return rows


@app.get("/api/inspections/{insp_id}")
def get_inspection(insp_id: str):
    insp = db.one("SELECT * FROM inspections WHERE id=?", (insp_id,))
    if not insp:
        raise HTTPException(404, "Inspection not found")
    frames = db.query("SELECT * FROM frames WHERE inspection_id=? ORDER BY idx", (insp_id,))
    dets = db.query("SELECT * FROM detections WHERE inspection_id=? ORDER BY score DESC", (insp_id,))
    by_frame = defaultdict(list)
    for d in dets:
        d["crop_url"] = f"/media/{d['crop_path']}" if d["crop_path"] else None
        by_frame[d["frame_id"]].append(d)
    for f in frames:
        f["image_url"] = f"/media/{f['image_path']}"
        f["annotated_url"] = f"/media/{f['annotated_path']}"
        f["detections"] = by_frame.get(f["id"], [])
        f["max_score"] = max((d["score"] for d in f["detections"]), default=0)
    insp["video_url"] = f"/media/{insp['video_path']}" if insp.get("video_path") else None
    insp["frames"] = frames
    insp["detections"] = dets
    return insp


@app.get("/api/inspections/{insp_id}/status")
def status(insp_id: str):
    r = db.one("SELECT id, status, progress, message FROM inspections WHERE id=?", (insp_id,))
    if not r:
        raise HTTPException(404, "Inspection not found")
    return r


@app.delete("/api/inspections/{insp_id}")
def delete_inspection(insp_id: str):
    db.delete_inspection(insp_id)
    for d in (OUTPUT_DIR / insp_id, UPLOAD_DIR / insp_id):
        shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


@app.post("/api/inspections/{insp_id}/rerun")
def rerun(insp_id: str):
    """Analyse the originally uploaded media again (e.g. after a model update)."""
    insp = db.one("SELECT * FROM inspections WHERE id=?", (insp_id,))
    if not insp:
        raise HTTPException(404, "Inspection not found")
    if insp["status"] in ("queued", "processing"):
        raise HTTPException(409, "Inspection is already being analysed")
    files = sorted(p for p in (UPLOAD_DIR / insp_id).glob("*") if p.is_file())
    if not files:
        raise HTTPException(410, "The original uploads are no longer available")
    db.clear_results(insp_id)
    shutil.rmtree(OUTPUT_DIR / insp_id, ignore_errors=True)
    db.update("inspections", insp_id, status="queued", progress=0, message="Queued", summary=None,
              video_path=None, finished_at=None)
    pipeline.submit(insp_id, files, insp["settings"])
    return {"id": insp_id}


@app.get("/api/inspections/{insp_id}/report.pdf")
def report(insp_id: str):
    insp = db.one("SELECT * FROM inspections WHERE id=?", (insp_id,))
    if not insp or insp["status"] != "done":
        raise HTTPException(404, "Report not available")
    frames = db.query("SELECT * FROM frames WHERE inspection_id=? ORDER BY idx", (insp_id,))
    dets = db.query("SELECT * FROM detections WHERE inspection_id=?", (insp_id,))
    pdf = build_pdf(insp, frames, dets)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="road_report_{insp_id}.pdf"'})


CSV_COLS = ["inspection_id", "inspection", "road_name", "location", "lat", "lon", "source", "t_sec", "type", "code",
            "severity", "score", "confidence", "x1", "y1", "x2", "y2", "recommendation"]


@app.get("/api/export.csv")
def export_csv(inspection_id: str | None = None):
    sql = ("SELECT d.*, i.name iname, i.road_name, i.location, i.lat ilat, i.lon ilon, f.source_name, f.lat flat, "
           "f.lon flon FROM detections d JOIN inspections i ON i.id=d.inspection_id "
           "JOIN frames f ON f.id=d.frame_id")
    rows = db.query(sql + (" WHERE d.inspection_id=?" if inspection_id else "") + " ORDER BY i.created_at, d.score DESC",
                    (inspection_id,) if inspection_id else ())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for r in rows:
        w.writerow([r["inspection_id"], r["iname"], r["road_name"], r["location"],
                    r["flat"] if r["flat"] is not None else r["ilat"], r["flon"] if r["flon"] is not None else r["ilon"],
                    r["source_name"], r["t_sec"], CLASSES[r["cls"]]["label"], CLASSES[r["cls"]]["code"],
                    r["severity"], r["score"], r["conf"], r["x1"], r["y1"], r["x2"], r["y2"], r["recommendation"]])
    name = f"detections_{inspection_id or 'all'}.csv"
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


# ------------------------------------------------------------------ dashboard

@app.get("/api/stats")
def stats():
    insps = db.query("SELECT id, name, road_name, location, lat, lon, media_type, created_at, summary "
                     "FROM inspections WHERE status='done' ORDER BY created_at")
    dets = db.query("SELECT d.cls, d.severity, d.score, d.inspection_id FROM detections d "
                    "JOIN inspections i ON i.id=d.inspection_id WHERE i.status='done'")
    by_cls = Counter(d["cls"] for d in dets)
    by_sev = Counter(d["severity"] for d in dets)
    matrix = {c: {s: 0 for s in SEVERITY_LEVELS} for c in CLASSES}
    for d in dets:
        matrix[d["cls"]][d["severity"]] += 1
    rcis = [i["summary"]["rci"] for i in insps if i["summary"]]
    ratings = Counter(i["summary"]["rating"] for i in insps if i["summary"])
    per_day = defaultdict(lambda: {"inspections": 0, "defects": 0, "critical": 0})
    for i in insps:
        day = i["created_at"][:10]
        per_day[day]["inspections"] += 1
        per_day[day]["defects"] += i["summary"]["total"]
        per_day[day]["critical"] += i["summary"]["by_severity"].get("Critical", 0)
    hist = [0] * 10
    for d in dets:
        hist[min(int(d["score"] // 10), 9)] += 1
    worst = sorted((i for i in insps if i["summary"]), key=lambda i: i["summary"]["rci"])[:8]
    return {
        "roads_inspected": len(insps),
        "total_defects": len(dets),
        "potholes": by_cls.get("pothole", 0),
        "by_class": {c: by_cls.get(c, 0) for c in CLASSES},
        "by_severity": {s: by_sev.get(s, 0) for s in SEVERITY_LEVELS},
        "matrix": matrix,
        "pothole_severity": matrix["pothole"],
        "avg_rci": round(sum(rcis) / len(rcis), 1) if rcis else None,
        "ratings": dict(ratings),
        "needs_repair": sum(1 for i in insps if i["summary"] and i["summary"]["priority"].startswith(("Immediate", "Short"))),
        "score_hist": hist,
        "per_day": [{"day": k, **v} for k, v in sorted(per_day.items())],
        "worst": [{"id": i["id"], "name": i["name"], "road_name": i["road_name"], "location": i["location"],
                   "rci": i["summary"]["rci"], "rating": i["summary"]["rating"], "priority": i["summary"]["priority"],
                   "critical": i["summary"]["by_severity"].get("Critical", 0), "total": i["summary"]["total"]}
                  for i in worst],
        "map": [{"id": i["id"], "name": i["name"], "lat": i["lat"], "lon": i["lon"], "rci": i["summary"]["rci"],
                 "rating": i["summary"]["rating"], "total": i["summary"]["total"]}
                for i in insps if i["lat"] is not None and i["lon"] is not None and i["summary"]],
    }
