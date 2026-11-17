import os
import sys
import uuid
import re
import json
import time
import shutil
import threading
import urllib.request
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, Response

app = Flask(__name__)

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
DOWNLOADS_DIR = BASE_DIR / "downloads"
DOWNLOADS_DIR.mkdir(exist_ok=True)
SDCARD_DOWNLOAD = Path("/sdcard/Download")

# Task Storage
tasks = {}
tasks_lock = threading.Lock()

@app.before_request
def handle_preflight():
    if request.method == "OPTIONS":
        res = Response()
        res.headers["Access-Control-Allow-Origin"] = "*"
        res.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Requested-With"
        res.headers["Access-Control-Allow-Methods"] = "GET,PUT,POST,DELETE,OPTIONS"
        return res

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type,Authorization,X-Requested-With"
    response.headers["Access-Control-Allow-Methods"] = "GET,PUT,POST,DELETE,OPTIONS"
    return response

@app.route("/health", methods=["GET", "OPTIONS"])
@app.route("/api/health", methods=["GET", "OPTIONS"])
def health_check():
    return jsonify({
        "status": "healthy",
        "app": "Ishan Studio Pro Engine",
        "version": "1.0.2"
    }), 200

def detect_platform(url):
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube", "youtube"
    elif "instagram.com" in u:
        return "Instagram", "instagram"
    elif "tiktok.com" in u:
        return "TikTok", "tiktok"
    elif "pinterest.com" in u or "pin.it" in u:
        return "Pinterest", "pinterest"
    return "Social Media", "generic"

def format_duration(seconds):
    if not seconds:
        return "00:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"

def format_bytes(bytes_count):
    if not bytes_count:
        return "N/A"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_count < 1024:
            return f"{bytes_count:.1f} {unit}"
        bytes_count /= 1024
    return f"{bytes_count:.1f} TB"

def get_ydl_base_opts():
    return {
        "quiet": True,
        "no_warnings": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "web_creator", "mweb"],
                "player_skip": ["webpage", "configs"]
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def analyze_url():
    import yt_dlp

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "Please paste a valid video URL."}), 400

    platform_name, platform_code = detect_platform(url)

    ydl_opts = get_ydl_base_opts()
    ydl_opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if not info:
                return jsonify({"error": "Unable to retrieve video information. Please verify the link."}), 404

            title = info.get("title", "Video")
            uploader = info.get("uploader") or info.get("channel") or info.get("creator") or platform_name
            duration = format_duration(info.get("duration"))
            thumbnail = info.get("thumbnail") or ""
            if not thumbnail and info.get("thumbnails"):
                thumbnail = info.get("thumbnails")[-1].get("url", "")

            views = f"{info.get('view_count', 0):,}" if info.get("view_count") else "N/A"
            formats = info.get("formats", [])
            video_options = []

            if platform_code == "youtube":
                # Available Heights
                available_heights = {f.get("height") for f in formats if f.get("height")}

                # Resolution target hierarchy with 60FPS support
                targets = [
                    {"height": 2160, "label": "4K Ultra HD (2160p)", "badge": "4K REAL", "gold": True},
                    {"height": 1440, "label": "2K Quad HD (1440p)", "badge": "2K HD", "gold": False},
                    {"height": 1080, "label": "Full HD (1080p)", "badge": "1080p", "gold": False},
                    {"height": 720, "label": "High Definition (720p)", "badge": "720p", "gold": False},
                    {"height": 480, "label": "Standard Quality (480p)", "badge": "480p", "gold": False},
                    {"height": 360, "label": "Mobile Saver (360p)", "badge": "360p", "gold": False},
                ]

                # Detect maximum FPS per height
                fps_map = {}
                for f in formats:
                    h = f.get("height")
                    fps = f.get("fps")
                    if h and fps:
                        fps_map[h] = max(fps_map.get(h, 0), int(fps))

                for target in targets:
                    h = target["height"]
                    if any(curr_h and curr_h >= h for curr_h in available_heights):
                        max_fps = max([fps_map.get(curr_h, 30) for curr_h in available_heights if curr_h and curr_h >= h] or [30])
                        fps_label = f" • {max_fps}FPS" if max_fps >= 50 else ""
                        fps_badge = f"{target['badge']} {max_fps}FPS" if max_fps >= 50 else target["badge"]

                        video_options.append({
                            "type": "video",
                            "height": h,
                            "fps": max_fps,
                            "label": f"{target['label']}{fps_label}",
                            "badge": fps_badge,
                            "is_gold": target["gold"],
                            "ext": "mp4",
                            "format_spec": f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
                        })

                if not video_options:
                    video_options.append({
                        "type": "video",
                        "height": 1080,
                        "label": "Best Available YouTube Quality (MP4)",
                        "badge": "HD",
                        "is_gold": False,
                        "ext": "mp4",
                        "format_spec": "bestvideo+bestaudio/best"
                    })
            else:
                # Direct pre-muxed streams for TikTok, Instagram, Facebook, Pinterest, X
                video_options.append({
                    "type": "video",
                    "height": 1080,
                    "fps": 60,
                    "label": f"Original HD {platform_name} Video (MP4)",
                    "badge": "ORIGINAL HD",
                    "is_gold": True,
                    "ext": "mp4",
                    "format_spec": "best/b"
                })
                if platform_code == "facebook":
                    video_options.append({
                        "type": "video",
                        "height": 480,
                        "fps": 30,
                        "label": "Standard Quality Video (SD MP4)",
                        "badge": "SD",
                        "is_gold": False,
                        "ext": "mp4",
                        "format_spec": "worst/b"
                    })

            audio_options = [
                {
                    "type": "audio",
                    "label": "High Quality MP3 Audio (320kbps)",
                    "badge": "MP3",
                    "ext": "mp3",
                    "codec": "mp3",
                    "format_spec": "bestaudio/best"
                },
                {
                    "type": "audio",
                    "label": "Original Audio Track (M4A)",
                    "badge": "M4A",
                    "ext": "m4a",
                    "codec": "m4a",
                    "format_spec": "bestaudio[ext=m4a]/bestaudio/best"
                }
            ]

            # Check if transcript / captions might exist
            has_subtitles = bool(info.get("subtitles") or info.get("automatic_captions"))

            return jsonify({
                "id": info.get("id"),
                "title": title,
                "platform": platform_name,
                "platform_code": platform_code,
                "uploader": uploader,
                "duration": duration,
                "thumbnail": thumbnail,
                "views": views,
                "video_options": video_options,
                "audio_options": audio_options,
                "has_transcript": has_subtitles,
                "url": url
            })

    except Exception as e:
        return jsonify({"error": f"ত্রুটি: {str(e)}"}), 500

@app.route("/api/transcript", methods=["POST"])
def get_transcript():
    import yt_dlp

    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()

    if not url:
        return jsonify({"error": "ভিডিও লিঙ্ক প্রদান করুন।"}), 400

    ydl_opts = get_ydl_base_opts()
    ydl_opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}

            # Prioritize Bengali then English then any available
            target_lang = None
            available_subs = None

            for lang in ["bn", "en", "en-orig", "hi"]:
                if lang in subs:
                    target_lang = lang
                    available_subs = subs[lang]
                    break
                elif lang in auto_subs:
                    target_lang = lang
                    available_subs = auto_subs[lang]
                    break

            if not available_subs:
                # Pick first available language
                if subs:
                    target_lang = list(subs.keys())[0]
                    available_subs = subs[target_lang]
                elif auto_subs:
                    target_lang = list(auto_subs.keys())[0]
                    available_subs = auto_subs[target_lang]

            if not available_subs:
                return jsonify({
                    "found": False,
                    "message": "এই ভিডিওটিতে কোনো সাবটাইটেল বা ট্রান্সক্রিপ্ট পাওয়া যায়নি।"
                })

            # Find json3 or vtt
            vtt_item = next((s for s in available_subs if s.get("ext") in ["json3", "vtt"]), available_subs[0])
            sub_url = vtt_item.get("url")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            req = urllib.request.Request(sub_url, headers=headers)
            with urllib.request.urlopen(req) as resp:
                content = resp.read().decode("utf-8")

            # Parse content
            transcript_lines = []
            if vtt_item.get("ext") == "json3" or content.strip().startswith("{"):
                try:
                    js = json.loads(content)
                    events = js.get("events", [])
                    for ev in events:
                        segs = ev.get("segs", [])
                        text = "".join(s.get("utf8", "") for s in segs).strip()
                        start_ms = ev.get("tStartMs", 0)
                        if text and text != "\n":
                            time_str = format_duration(start_ms / 1000)
                            transcript_lines.append(f"[{time_str}] {text}")
                except Exception:
                    pass

            if not transcript_lines:
                # Parse VTT / SRT format
                raw_lines = content.split("\n")
                clean_lines = []
                for line in raw_lines:
                    line = line.strip()
                    if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line or line.isdigit():
                        continue
                    # remove html tags like <c> etc.
                    clean = re.sub(r"<[^>]+>", "", line).strip()
                    if clean and clean not in clean_lines[-3:]:
                        clean_lines.append(clean)
                transcript_lines = clean_lines

            full_text = "\n".join(transcript_lines)
            return jsonify({
                "found": True,
                "language": target_lang,
                "line_count": len(transcript_lines),
                "transcript": full_text
            })

    except Exception as e:
        return jsonify({"found": False, "error": str(e), "message": "No subtitles or transcript found for this video."}), 200

def run_download_worker(task_id, url, format_spec, is_audio, audio_codec, save_to_sdcard, title):
    import yt_dlp

    def progress_hook(d):
        with tasks_lock:
            task = tasks.get(task_id)
            if not task:
                return

            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0
                speed = d.get("speed") or 0
                eta = d.get("eta") or 0

                pct = round((downloaded / total * 100), 1) if total > 0 else 0

                if speed > 1024 * 1024:
                    speed_str = f"{speed / (1024 * 1024):.1f} MB/s"
                elif speed > 1024:
                    speed_str = f"{speed / 1024:.0f} KB/s"
                else:
                    speed_str = "0 KB/s"

                eta_str = f"{eta}s" if eta else "Calculating..."

                task["status"] = "downloading"
                task["progress"] = pct
                task["speed"] = speed_str
                task["eta"] = eta_str
                task["downloaded_bytes"] = format_bytes(downloaded)
                task["total_bytes"] = format_bytes(total)

            elif d["status"] == "finished":
                task["status"] = "processing"
                task["progress"] = 99
                task["speed"] = "Finalizing (FFmpeg)..."
                task["eta"] = "Almost done"

    safe_title = re.sub(r'[\/:*?"<>|]', '_', title)[:80]
    output_template = str(DOWNLOADS_DIR / f"{task_id}_{safe_title}.%(ext)s")

    ydl_opts = get_ydl_base_opts()
    ydl_opts.update({
        "format": format_spec or "best/b",
        "outtmpl": output_template,
        "progress_hooks": [progress_hook],
    })

    if is_audio:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": audio_codec,
            "preferredquality": "320" if audio_codec == "mp3" else "192",
        }]
    else:
        ydl_opts["merge_output_format"] = "mp4"
        if "youtube.com" in url.lower() or "youtu.be" in url.lower():
            ydl_opts["postprocessor_args"] = {
                "ffmpeg": ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart"]
            }

    try:
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as first_err:
            err_str = str(first_err).lower()
            if "format" in err_str or "requested format" in err_str or "extractor" in err_str:
                # Universal fallback without postprocessor restrictions
                fallback_opts = get_ydl_base_opts()
                fallback_opts.update({
                    "format": "best/b",
                    "outtmpl": output_template,
                    "progress_hooks": [progress_hook],
                    "merge_output_format": "mp4",
                })
                with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                    ydl.download([url])
            else:
                raise first_err

        files = list(DOWNLOADS_DIR.glob(f"{task_id}_*"))
        if not files:
            raise Exception("Downloaded media file could not be found.")

        final_path = files[0]
        final_filename = final_path.name.replace(f"{task_id}_", "")

        saved_sdcard_path = None
        if save_to_sdcard and SDCARD_DOWNLOAD.exists():
            target_path = SDCARD_DOWNLOAD / final_filename
            try:
                shutil.copy2(final_path, target_path)
                saved_sdcard_path = str(target_path)
            except Exception as se:
                print(f"SDCard copy error: {se}", file=sys.stderr)

        with tasks_lock:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["progress"] = 100
            tasks[task_id]["filepath"] = str(final_path)
            tasks[task_id]["filename"] = final_filename
            tasks[task_id]["filesize"] = format_bytes(final_path.stat().st_size)
            tasks[task_id]["saved_sdcard_path"] = saved_sdcard_path

    except Exception as e:
        raw = str(e)
        if "Blocked" in raw or "blocked" in raw:
            clean_msg = "Platform temporarily rate-limited this request. Please retry in a few moments."
        elif "private" in raw.lower():
            clean_msg = "This video is private or unavailable on the platform."
        elif "format" in raw.lower():
            clean_msg = "The requested video format was unavailable. Please try downloading again."
        else:
            clean_msg = f"Download error: {raw[:100]}"
        with tasks_lock:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = clean_msg

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    format_spec = data.get("format_spec", "bestvideo+bestaudio/best")
    is_audio = data.get("is_audio", False)
    audio_codec = data.get("audio_codec", "mp3")
    save_to_sdcard = data.get("save_to_sdcard", True)
    title = data.get("title", "Video")

    if not url:
        return jsonify({"error": "লিঙ্ক প্রদান করা আবশ্যক।"}), 400

    task_id = str(uuid.uuid4())[:8]

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "status": "starting",
            "progress": 0,
            "speed": "শুরু হচ্ছে...",
            "eta": "...",
            "downloaded_bytes": "0 B",
            "total_bytes": "হিসাব করা হচ্ছে...",
            "filename": None,
            "filepath": None,
            "title": title,
            "saved_sdcard_path": None,
            "error": None,
            "created_at": time.time()
        }

    t = threading.Thread(
        target=run_download_worker,
        args=(task_id, url, format_spec, is_audio, audio_codec, save_to_sdcard, title),
        daemon=True
    )
    t.start()

    return jsonify({"task_id": task_id})

@app.route("/api/progress/<task_id>")
def get_progress(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return jsonify({"error": "টাস্ক পাওয়া যায়নি"}), 404
        return jsonify(task)

@app.route("/api/file/<task_id>")
def download_file(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return "Task not found", 404
        if task.get("status") != "completed" or not task.get("filepath"):
            return "File is not ready yet", 400

        filepath = Path(task["filepath"])
        filename = task.get("filename", filepath.name)

    if not filepath.exists():
        return "File does not exist on disk", 404

    return send_file(
        str(filepath),
        as_attachment=True,
        download_name=filename
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"\n=======================================================")
    print(f" ✨ Ishan Studio Pro Engine is LIVE!")
    print(f" 📱 Mobile App URL: http://localhost:{port}")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
