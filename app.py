import os
import sys
import uuid
import re
import json
import time
import shutil
import threading
import subprocess
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
        "version": "1.0.6",
        "features": ["4K_60FPS", "2X_UPSCALE", "TRANSCRIPTS", "NO_WATERMARK"]
    }), 200

def detect_platform(url):
    u = url.lower()
    if "youtube.com" in u or "youtu.be" in u:
        return "YouTube", "youtube"
    elif "instagram.com" in u:
        return "Instagram", "instagram"
    elif "tiktok.com" in u:
        return "TikTok", "tiktok"
    elif "facebook.com" in u or "fb.watch" in u or "fb.com" in u:
        return "Facebook", "facebook"
    elif "twitter.com" in u or "x.com" in u:
        return "X (Twitter)", "twitter"
    elif "pinterest.com" in u or "pin.it" in u:
        return "Pinterest", "pinterest"
    elif "threads.net" in u:
        return "Threads", "threads"
    elif "reddit.com" in u:
        return "Reddit", "reddit"
    return "Social Media", "generic"

def expand_url(url):
    """Expands shortened URLs (vt.tiktok.com, vm.tiktok.com, pin.it, youtu.be, fb.watch)"""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            return resp.geturl()
    except Exception:
        return url

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
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "cachedir": False,
        "noplaylist": True,
        "prefer_ffmpeg": True,
        "extractor_args": {
            "youtube": {
                "player_client": ["android", "ios", "mweb", "web"],
                "player_skip": ["webpage", "configs"]
            },
            "instagram": {
                "api_version": "v1"
            }
        },
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
        }
    }

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/analyze", methods=["POST"])
def analyze_url():
    import yt_dlp

    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "").strip()

    if not raw_url:
        return jsonify({"error": "Please paste a valid video URL."}), 400

    url = expand_url(raw_url)
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

            # Detect native base resolution
            orig_h = info.get("height") or 0
            orig_w = info.get("width") or 0
            # For formats, find highest available height
            for f in formats:
                h = f.get("height") or 0
                w = f.get("width") or 0
                if h > orig_h:
                    orig_h = h
                if w > orig_w:
                    orig_w = w

            base_res = min(orig_h, orig_w) if (orig_h and orig_w) else (orig_h or 1080)
            if base_res < 360:
                base_res = 1080

            video_options = []

            if platform_code == "youtube":
                available_heights = {f.get("height") for f in formats if f.get("height")}
                fps_map = {}
                for f in formats:
                    h = f.get("height")
                    fps = f.get("fps")
                    if h and fps:
                        fps_map[h] = max(fps_map.get(h, 0), int(fps))

                # If native 4K exists:
                if any(h and h >= 2160 for h in available_heights):
                    max_fps = fps_map.get(2160, 60)
                    fps_label = f" • {max_fps}FPS" if max_fps >= 50 else ""
                    video_options.append({
                        "type": "video",
                        "height": 2160,
                        "fps": max_fps,
                        "label": f"4K Ultra HD (2160p){fps_label}",
                        "badge": "🌟 4K NATIVE",
                        "desc": "Original 3840x2160 Ultra HD Stream",
                        "is_gold": True,
                        "upscale": 0,
                        "ext": "mp4",
                        "format_spec": "bestvideo[height<=2160]+bestaudio/best[height<=2160]/best"
                    })
                else:
                    # Provide 4K AI Super-Resolution option
                    video_options.append({
                        "type": "video",
                        "height": 2160,
                        "fps": 60,
                        "label": "4K Ultra HD (2160p) • AI Super-Resolution",
                        "badge": "👑 4K ULTRA",
                        "desc": "Studio 4K Master Render (FFmpeg Lanczos)",
                        "is_gold": True,
                        "upscale": 2160,
                        "ext": "mp4",
                        "format_spec": "bestvideo+bestaudio/best"
                    })

                # If native 2K exists:
                if any(h and h >= 1440 for h in available_heights):
                    max_fps = fps_map.get(1440, 60)
                    fps_label = f" • {max_fps}FPS" if max_fps >= 50 else ""
                    video_options.append({
                        "type": "video",
                        "height": 1440,
                        "fps": max_fps,
                        "label": f"2K Quad HD (1440p){fps_label}",
                        "badge": "🚀 2K NATIVE",
                        "desc": "Original 2560x1440 Quad HD Stream",
                        "is_gold": True,
                        "upscale": 0,
                        "ext": "mp4",
                        "format_spec": "bestvideo[height<=1440]+bestaudio/best[height<=1440]/best"
                    })
                else:
                    # 2K 2X Double Resolution Boost
                    video_options.append({
                        "type": "video",
                        "height": 1440,
                        "fps": 60,
                        "label": "2K Quad HD (1440p) • 2X Resolution Boost",
                        "badge": "🚀 2K 2X BOOST",
                        "desc": "2X Double-Clarity Upscale (1440p)",
                        "is_gold": True,
                        "upscale": 1440,
                        "ext": "mp4",
                        "format_spec": "bestvideo+bestaudio/best"
                    })

                # Native 1080p
                if any(h and h >= 1080 for h in available_heights):
                    max_fps = fps_map.get(1080, 30)
                    fps_label = f" • {max_fps}FPS" if max_fps >= 50 else ""
                    video_options.append({
                        "type": "video",
                        "height": 1080,
                        "fps": max_fps,
                        "label": f"Full HD (1080p){fps_label}",
                        "badge": "1080p HD",
                        "desc": "Lossless 1080p Video + High-Bitrate Audio",
                        "is_gold": False,
                        "upscale": 0,
                        "ext": "mp4",
                        "format_spec": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
                    })

                # Native 720p
                if any(h and h >= 720 for h in available_heights):
                    video_options.append({
                        "type": "video",
                        "height": 720,
                        "fps": 30,
                        "label": "High Definition (720p)",
                        "badge": "720p",
                        "desc": "Fast Standard HD Stream",
                        "is_gold": False,
                        "upscale": 0,
                        "ext": "mp4",
                        "format_spec": "bestvideo[height<=720]+bestaudio/best[height<=720]/best"
                    })

                # Fast Original
                video_options.append({
                    "type": "video",
                    "height": 1080,
                    "fps": 30,
                    "label": "Fast Original YouTube Stream",
                    "badge": "⚡ FAST",
                    "desc": "Direct Raw Stream • Instant Download",
                    "is_gold": False,
                    "upscale": 0,
                    "ext": "mp4",
                    "format_spec": "best"
                })

            else:
                # Platforms: TikTok, Instagram Reels, Facebook, Pinterest, X, Threads
                # Ishan's requirement: 4K render, 2K (2x double render for 1080p/720p), 1080p, and Fast Original!

                # Option 1: 4K Ultra HD Super-Resolution
                video_options.append({
                    "type": "video",
                    "height": 2160,
                    "fps": 60,
                    "label": f"4K Ultra HD (2160p) • {platform_name} AI Master",
                    "badge": "👑 4K ULTRA",
                    "desc": "Studio 4K Super-Resolution (FFmpeg Lanczos Engine)",
                    "is_gold": True,
                    "upscale": 2160,
                    "ext": "mp4",
                    "format_spec": "best/b"
                })

                # Option 2: 2K Quad HD (1440p) - 2X Double-Resolution Boost
                video_options.append({
                    "type": "video",
                    "height": 1440,
                    "fps": 60,
                    "label": f"2K Quad HD (1440p) • 2X Resolution Boost",
                    "badge": "🚀 2K 2X BOOST",
                    "desc": "Double-Resolution 2X Clarity Boost (2560x1440)",
                    "is_gold": True,
                    "upscale": 1440,
                    "ext": "mp4",
                    "format_spec": "best/b"
                })

                # Option 3: Full HD (1080p) Pro
                video_options.append({
                    "type": "video",
                    "height": 1080,
                    "fps": 30,
                    "label": f"Full HD (1080p) • Enhanced Clarity",
                    "badge": "🌟 1080p PRO",
                    "desc": "High Bitrate 1080p MP4 • Zero Watermark",
                    "is_gold": False,
                    "upscale": 1080,
                    "ext": "mp4",
                    "format_spec": "best/b"
                })

                # Option 4: Fast Original Source (Instant Speed, No Re-encoding)
                video_options.append({
                    "type": "video",
                    "height": orig_h or 1080,
                    "fps": 30,
                    "label": f"Original {platform_name} Stream (Fast Download)",
                    "badge": "⚡ FAST ORIGINAL",
                    "desc": "Direct Raw Lossless MP4 • Instant Download",
                    "is_gold": False,
                    "upscale": 0,
                    "ext": "mp4",
                    "format_spec": "best/b"
                })

            audio_options = [
                {
                    "type": "audio",
                    "label": "High Quality MP3 Audio (320kbps)",
                    "badge": "MP3 320K",
                    "ext": "mp3",
                    "codec": "mp3",
                    "format_spec": "bestaudio/best"
                },
                {
                    "type": "audio",
                    "label": "Original Audio Track (M4A / AAC)",
                    "badge": "M4A HQ",
                    "ext": "m4a",
                    "codec": "m4a",
                    "format_spec": "bestaudio[ext=m4a]/bestaudio/best"
                }
            ]

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
        return jsonify({"error": f"Error analyzing media: {str(e)}"}), 500

@app.route("/api/transcript", methods=["POST"])
def get_transcript():
    import yt_dlp

    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "").strip()

    if not raw_url:
        return jsonify({"error": "Video link URL is required."}), 400

    url = expand_url(raw_url)
    ydl_opts = get_ydl_base_opts()
    ydl_opts["skip_download"] = True

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            subs = info.get("subtitles") or {}
            auto_subs = info.get("automatic_captions") or {}

            target_lang = None
            available_subs = None

            for lang in ["en", "bn", "hi", "en-orig"]:
                if lang in subs:
                    target_lang = lang
                    available_subs = subs[lang]
                    break
                elif lang in auto_subs:
                    target_lang = lang
                    available_subs = auto_subs[lang]
                    break

            if not available_subs:
                if subs:
                    target_lang = list(subs.keys())[0]
                    available_subs = subs[target_lang]
                elif auto_subs:
                    target_lang = list(auto_subs.keys())[0]
                    available_subs = auto_subs[target_lang]

            if not available_subs:
                return jsonify({
                    "found": False,
                    "message": "No subtitles or transcript found for this video."
                })

            vtt_item = next((s for s in available_subs if s.get("ext") in ["json3", "vtt"]), available_subs[0])
            sub_url = vtt_item.get("url")

            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            }
            req = urllib.request.Request(sub_url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")

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
                raw_lines = content.split("\n")
                clean_lines = []
                for line in raw_lines:
                    line = line.strip()
                    if not line or line.startswith("WEBVTT") or line.startswith("NOTE") or "-->" in line or line.isdigit():
                        continue
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

def run_download_worker(task_id, url, format_spec, is_audio, audio_codec, upscale_target, save_to_sdcard, title):
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
                task["progress"] = min(pct, 79 if (upscale_target > 0 and not is_audio) else 95)
                task["speed"] = speed_str
                task["eta"] = eta_str
                task["downloaded_bytes"] = format_bytes(downloaded)
                task["total_bytes"] = format_bytes(total)

            elif d["status"] == "finished":
                task["status"] = "processing"
                task["progress"] = 80 if (upscale_target > 0 and not is_audio) else 95
                task["speed"] = "Finalizing (FFmpeg)..."
                task["eta"] = "Almost done"

    safe_title = re.sub(r'[\/:*?"<>|]', '_', title)[:80].strip() or "video"

    is_upscaling = (upscale_target > 0) and not is_audio

    # Destination template
    if is_upscaling:
        output_template = str(DOWNLOADS_DIR / f"{task_id}_raw.%(ext)s")
    else:
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
        # Step 1: Download
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception as first_err:
            err_str = str(first_err).lower()
            if "format" in err_str or "requested format" in err_str or "extractor" in err_str:
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

        # Step 2: Locate downloaded file
        if is_upscaling:
            raw_candidates = list(DOWNLOADS_DIR.glob(f"{task_id}_raw.*"))
            if not raw_candidates:
                raise Exception("Downloaded source file could not be located.")
            raw_path = raw_candidates[0]

            with tasks_lock:
                tasks[task_id]["status"] = "processing"
                tasks[task_id]["progress"] = 82
                tasks[task_id]["speed"] = f"FFmpeg 2X Lanczos Rendering ({upscale_target}p)..."
                tasks[task_id]["eta"] = "Sharpening & Upscaling..."

            upscaled_file = DOWNLOADS_DIR / f"{task_id}_{safe_title}_{upscale_target}p.mp4"
            # Lanczos scaling + gentle unsharp filter
            scale_filter = f"scale='if(gte(ih,iw),{upscale_target},-2)':'if(gte(ih,iw),-2,{upscale_target})':flags=lanczos,unsharp=5:5:0.6:5:5:0.0"

            cmd = [
                "ffmpeg", "-y", "-i", str(raw_path),
                "-vf", scale_filter,
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                "-c:a", "aac", "-b:a", "320k",
                "-movflags", "+faststart",
                str(upscaled_file)
            ]

            try:
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300)
                if proc.returncode == 0 and upscaled_file.exists() and upscaled_file.stat().st_size > 1000:
                    try:
                        raw_path.unlink(missing_ok=True)
                    except Exception:
                        pass
                    final_path = upscaled_file
                else:
                    final_path = raw_path
            except Exception as ffmpeg_err:
                print(f"Upscaling fallback notice: {ffmpeg_err}", file=sys.stderr)
                final_path = raw_path
        else:
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
                print(f"SDCard copy notice: {se}", file=sys.stderr)

        with tasks_lock:
            tasks[task_id]["status"] = "completed"
            tasks[task_id]["progress"] = 100
            tasks[task_id]["speed"] = "Finished"
            tasks[task_id]["eta"] = "0s"
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
            clean_msg = f"Download notice: {raw[:100]}"
        with tasks_lock:
            tasks[task_id]["status"] = "error"
            tasks[task_id]["error"] = clean_msg

@app.route("/api/download", methods=["POST"])
def start_download():
    data = request.get_json(silent=True) or {}
    raw_url = data.get("url", "").strip()
    format_spec = data.get("format_spec", "best/b")
    is_audio = data.get("is_audio", False)
    audio_codec = data.get("audio_codec", "mp3")
    upscale_target = int(data.get("upscale_target") or 0)
    save_to_sdcard = data.get("save_to_sdcard", True)
    title = data.get("title", "Video")

    if not raw_url:
        return jsonify({"error": "Video link URL is required."}), 400

    url = expand_url(raw_url)
    task_id = str(uuid.uuid4())[:8]

    with tasks_lock:
        tasks[task_id] = {
            "task_id": task_id,
            "status": "starting",
            "progress": 0,
            "speed": "Starting...",
            "eta": "...",
            "downloaded_bytes": "0 B",
            "total_bytes": "Calculating...",
            "filename": None,
            "filepath": None,
            "title": title,
            "saved_sdcard_path": None,
            "error": None,
            "created_at": time.time()
        }

    t = threading.Thread(
        target=run_download_worker,
        args=(task_id, url, format_spec, is_audio, audio_codec, upscale_target, save_to_sdcard, title),
        daemon=True
    )
    t.start()

    return jsonify({"task_id": task_id})

@app.route("/api/progress/<task_id>")
def get_progress(task_id):
    with tasks_lock:
        task = tasks.get(task_id)
        if not task:
            return jsonify({"error": "Task not found"}), 404
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
    print(f" ✨ Ishan Studio Pro Engine is LIVE! (v1.0.6 2X Upscale)")
    print(f" 📱 Mobile App URL: http://localhost:{port}")
    print(f"=======================================================\n")
    app.run(host="0.0.0.0", port=port, debug=False)
