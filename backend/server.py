from flask import (
    Flask,
    request,
    jsonify,
    send_from_directory,
    send_file
)

from pathlib import Path
from datetime import datetime
import threading
import uuid
import json
import re
import shutil

import yt_dlp


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

FRONTEND_DIR = PROJECT_DIR / "frontend"
MUSIC_DIR = BASE_DIR / "music"
CACHE_FILE = BASE_DIR / "cache.json"

MUSIC_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# DOWNLOAD STATE
# ============================================================

jobs = {}
jobs_lock = threading.Lock()


# ============================================================
# CACHE
# ============================================================

def load_cache():
    if not CACHE_FILE.exists():
        return []

    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save_cache(cache):
    temporary = CACHE_FILE.with_suffix(".tmp")

    with open(
        temporary,
        "w",
        encoding="utf-8"
    ) as f:
        json.dump(
            cache,
            f,
            indent=2,
            ensure_ascii=False
        )

    temporary.replace(CACHE_FILE)


cache_lock = threading.Lock()


def get_cached(video_id):
    with cache_lock:
        cache = load_cache()

        for item in cache:
            if item.get("video_id") == video_id:
                path = Path(item.get("path", ""))

                if path.exists():
                    return item

        return None


def add_cache_entry(
    video_id,
    title,
    channel,
    thumbnail,
    path
):
    with cache_lock:
        cache = load_cache()

        cache = [
            item
            for item in cache
            if item.get("video_id") != video_id
        ]

        entry = {
            "cache_id": str(uuid.uuid4()),
            "video_id": video_id,
            "title": title,
            "channel": channel,
            "thumbnail": thumbnail,
            "path": str(path),
            "created_at": datetime.now().isoformat()
        }

        cache.append(entry)

        save_cache(cache)

        return entry


# ============================================================
# HELPERS
# ============================================================

def safe_filename(value):
    value = str(value or "Unknown")

    value = re.sub(
        r'[<>:"/\\|?*\x00-\x1f]',
        "",
        value
    )

    value = value.strip()

    if not value:
        value = "Unknown"

    return value[:120]


def valid_youtube_url(url):
    if not url:
        return False

    url = url.lower()

    allowed = (
        "youtube.com/",
        "www.youtube.com/",
        "music.youtube.com/",
        "youtu.be/"
    )

    return any(host in url for host in allowed)


def find_downloaded_file(job_id):
    matches = list(
        MUSIC_DIR.glob(f".tmp_{job_id}.*")
    )

    if matches:
        return matches[0]

    return None


def update_job(job_id, **values):
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(values)


# ============================================================
# DOWNLOAD
# ============================================================

def download_song(
    job_id,
    song,
    keep
):
    video_id = song.get("id")
    title = song.get("title", "Unknown")
    channel = song.get("channel", "")
    thumbnail = song.get("thumbnail", "")
    url = song.get("url")

    try:
        if not valid_youtube_url(url):
            raise ValueError(
                "Only YouTube URLs are supported."
            )

        # ----------------------------------------------------
        # Existing cache
        # ----------------------------------------------------

        if keep:
            existing = get_cached(video_id)

            if existing:
                update_job(
                    job_id,
                    status="finished",
                    progress=100,
                    path=existing["path"],
                    cached=True
                )

                return

        # ----------------------------------------------------
        # Temporary output
        # ----------------------------------------------------

        output_template = (
            MUSIC_DIR /
            f".tmp_{job_id}.%(ext)s"
        )

        def progress_hook(data):
            status = data.get("status")

            if status == "downloading":
                total = (
                    data.get("total_bytes")
                    or data.get("total_bytes_estimate")
                )

                downloaded = (
                    data.get("downloaded_bytes", 0)
                )

                progress = 0

                if total:
                    progress = (
                        downloaded / total
                    ) * 100

                update_job(
                    job_id,
                    status="downloading",
                    progress=round(
                        min(progress, 100),
                        1
                    )
                )

            elif status == "finished":
                update_job(
                    job_id,
                    status="processing",
                    progress=99
                )

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,

            "format":
                "bestaudio[ext=m4a]/"
                "bestaudio[acodec^=mp4a]/"
                "bestaudio",

            "outtmpl": str(output_template),

            "progress_hooks": [
                progress_hook
            ],

            "restrictfilenames": True,

            "http_headers": {
                "User-Agent":
                    "Mozilla/5.0 "
                    "(Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 "
                    "(KHTML, like Gecko) "
                    "Chrome/140 Safari/537.36"
            }
        }

        update_job(
            job_id,
            status="starting",
            progress=0
        )

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.extract_info(
                url,
                download=True
            )

        downloaded_file = find_downloaded_file(
            job_id
        )

        if not downloaded_file:
            raise FileNotFoundError(
                "Downloaded audio file was not found."
            )

        # ----------------------------------------------------
        # Keep permanently
        # ----------------------------------------------------

        if keep:
            extension = downloaded_file.suffix

            permanent_name = (
                f"{safe_filename(video_id)} - "
                f"{safe_filename(title)}"
                f"{extension}"
            )

            permanent_path = (
                MUSIC_DIR / permanent_name
            )

            # Prevent collisions
            counter = 1

            while permanent_path.exists():
                permanent_name = (
                    f"{safe_filename(video_id)} - "
                    f"{safe_filename(title)} "
                    f"({counter})"
                    f"{extension}"
                )

                permanent_path = (
                    MUSIC_DIR / permanent_name
                )

                counter += 1

            shutil.move(
                str(downloaded_file),
                str(permanent_path)
            )

            entry = add_cache_entry(
                video_id,
                title,
                channel,
                thumbnail,
                permanent_path
            )

            update_job(
                job_id,
                status="finished",
                progress=100,
                path=str(permanent_path),
                cache_id=entry["cache_id"],
                cached=True
            )

        else:
            update_job(
                job_id,
                status="finished",
                progress=100,
                path=str(downloaded_file),
                cached=False
            )

    except Exception as exc:
        update_job(
            job_id,
            status="error",
            progress=0,
            error=str(exc)
        )


# ============================================================
# FRONTEND
# ============================================================

@app.get("/")
def index():
    return send_from_directory(
        FRONTEND_DIR,
        "index.html"
    )


@app.get("/<path:path>")
def frontend(path):
    return send_from_directory(
        FRONTEND_DIR,
        path
    )


# ============================================================
# SEARCH
# ============================================================

@app.get("/api/search")
def search():
    query = (
        request.args.get("q", "")
        .strip()
    )

    if not query:
        return jsonify({
            "success": True,
            "results": []
        })

    if len(query) > 200:
        return jsonify({
            "success": False,
            "error": "Search query is too long."
        }), 400

    options = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "skip_download": True,
        "noplaylist": True
    }

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(
                f"ytsearch12:{query}",
                download=False
            )

        results = []

        for item in info.get("entries", []):
            if not item:
                continue

            video_id = item.get("id")

            if not video_id:
                continue

            title = item.get(
                "title",
                "Unknown"
            )

            channel = (
                item.get("channel")
                or item.get("uploader")
                or "Unknown artist"
            )

            thumbnail = (
                item.get("thumbnail")
                or
                f"https://i.ytimg.com/vi/"
                f"{video_id}/hqdefault.jpg"
            )

            duration = item.get(
                "duration",
                0
            )

            results.append({
                "id": video_id,
                "title": title,
                "channel": channel,
                "thumbnail": thumbnail,
                "duration": duration,
                "url":
                    f"https://www.youtube.com/watch?v="
                    f"{video_id}"
            })

        return jsonify({
            "success": True,
            "results": results
        })

    except Exception as exc:
        return jsonify({
            "success": False,
            "error": str(exc)
        }), 500


# ============================================================
# DOWNLOAD START
# ============================================================

@app.post("/api/download")
def start_download():
    data = request.get_json(
        silent=True
    ) or {}

    song = data.get("song")
    keep = bool(data.get("keep", False))

    if not song:
        return jsonify({
            "success": False,
            "error": "Missing song."
        }), 400

    url = song.get("url")

    if not valid_youtube_url(url):
        return jsonify({
            "success": False,
            "error": "Invalid YouTube URL."
        }), 400

    job_id = str(uuid.uuid4())

    with jobs_lock:
        jobs[job_id] = {
            "status": "queued",
            "progress": 0,
            "path": None,
            "error": None,
            "keep": keep
        }

    thread = threading.Thread(
        target=download_song,
        args=(
            job_id,
            song,
            keep
        ),
        daemon=True
    )

    thread.start()

    return jsonify({
        "success": True,
        "job_id": job_id
    })


# ============================================================
# DOWNLOAD STATUS
# ============================================================

@app.get("/api/download/<job_id>")
def download_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({
            "success": False,
            "error": "Job not found."
        }), 404

    response = {
        "success": True,
        "status": job["status"],
        "progress": job["progress"],
        "error": job.get("error")
    }

    if (
        job["status"] == "finished"
        and job.get("path")
    ):
        response["url"] = (
            f"/api/file/{job_id}"
        )

    return jsonify(response)


# ============================================================
# FILE SERVING
# ============================================================

@app.get("/api/file/<job_id>")
def serve_job_file(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({
            "error": "File not found."
        }), 404

    path = job.get("path")

    if not path:
        return jsonify({
            "error": "File is not ready."
        }), 404

    file_path = Path(path)

    if not file_path.exists():
        return jsonify({
            "error": "File no longer exists."
        }), 404

    return send_file(
        file_path,
        conditional=True
    )


# ============================================================
# CLEANUP TEMP DOWNLOAD
# ============================================================

@app.post("/api/download/<job_id>/cleanup")
def cleanup_download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)

    if not job:
        return jsonify({
            "success": False
        }), 404

    if job.get("keep"):
        return jsonify({
            "success": True,
            "deleted": False
        })

    path = job.get("path")

    if path:
        try:
            file_path = Path(path)

            if file_path.exists():
                file_path.unlink()

        except Exception:
            pass

    with jobs_lock:
        jobs.pop(job_id, None)

    return jsonify({
        "success": True,
        "deleted": True
    })


# ============================================================
# CACHE API
# ============================================================

@app.get("/api/cache")
def list_cache():
    with cache_lock:
        cache = load_cache()

    valid = []

    for item in cache:
        path = Path(
            item.get("path", "")
        )

        if path.exists():
            clean = dict(item)
            clean.pop("path", None)

            clean["file_url"] = (
                f"/api/cache/file/"
                f"{item['cache_id']}"
            )

            valid.append(clean)

    return jsonify({
        "success": True,
        "songs": valid
    })


@app.get("/api/cache/file/<cache_id>")
def serve_cached_file(cache_id):
    with cache_lock:
        cache = load_cache()

    for item in cache:
        if item.get("cache_id") == cache_id:
            path = Path(
                item.get("path", "")
            )

            if not path.exists():
                break

            return send_file(
                path,
                conditional=True,
                as_attachment=False
            )

    return jsonify({
        "error": "Cached song not found."
    }), 404


@app.delete("/api/cache/<cache_id>")
def delete_cached_file(cache_id):
    with cache_lock:
        cache = load_cache()

        target = None

        for item in cache:
            if item.get("cache_id") == cache_id:
                target = item
                break

        if not target:
            return jsonify({
                "success": False,
                "error": "Cached song not found."
            }), 404

        path = Path(
            target.get("path", "")
        )

        try:
            if path.exists():
                path.unlink()
        except Exception:
            pass

        cache = [
            item
            for item in cache
            if item.get("cache_id") != cache_id
        ]

        save_cache(cache)

    return jsonify({
        "success": True
    })


@app.delete("/api/cache")
def clear_cache():
    with cache_lock:
        cache = load_cache()

        for item in cache:
            path = Path(
                item.get("path", "")
            )

            try:
                if path.exists():
                    path.unlink()
            except Exception:
                pass

        save_cache([])

    return jsonify({
        "success": True
    })


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    print()
    print("========================================")
    print("             PYSIC MUSIC")
    print("========================================")
    print()
    print("Frontend:")
    print("http://127.0.0.1:8000")
    print()
    print("Press CTRL+C to stop.")
    print()

    app.run(
        host="0.0.0.0",
        port=8000,
        debug=False,
        threaded=True
    )