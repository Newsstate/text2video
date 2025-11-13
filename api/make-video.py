# api/make-video.py

import os
import base64
import tempfile
import requests
from flask import Flask, request, jsonify
from moviepy.editor import *
from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# ---------- CONFIG FROM ENV ----------
WP_BASE_URL = os.environ.get("WP_BASE_URL", "").rstrip("/")
WP_USER = os.environ.get("WP_USER", "")
WP_APP_PASSWORD = os.environ.get("WP_APP_PASSWORD", "")

LANG = os.environ.get("TTS_LANG", "hi")      # hi = Hindi, en = English
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "5"))


@app.route("/", methods=["GET"])
def health():
    return "blog-to-video function is running. Use POST to generate a video."


@app.route("/", methods=["POST"])
def make_video():
    """
    This handler will be served at:
    https://<your-project>.vercel.app/api/make-video
    """
    try:
        data = request.get_json(force=True) or {}
    except Exception:
        data = {}

    print("Received data:", data)

    text = data.get("content", "")
    images = data.get("images", [])
    title = data.get("title", "Blog Video")
    post_id = str(data.get("post_id", "post"))

    if not text.strip():
        return jsonify({"error": "No content provided"}), 400

    # Use Vercel temp directory
    tmp_dir = tempfile.gettempdir()

    # ---------- 1. Generate TTS audio (gTTS) ----------
    audio_path = os.path.join(tmp_dir, f"audio_{post_id}.mp3")
    tts = gTTS(text=text, lang=LANG)
    tts.save(audio_path)

    # ---------- 2. Download / create images ----------
    image_files = []

    if images:
        for i, url in enumerate(images[:MAX_IMAGES]):
            try:
                resp = requests.get(url, timeout=15)
                resp.raise_for_status()
                filename = os.path.join(tmp_dir, f"img_{post_id}_{i}.jpg")
                with open(filename, "wb") as f:
                    f.write(resp.content)
                image_files.append(filename)
            except Exception as e:
                print("Image download error:", e)

    if not image_files:
        title_img = create_title_image(title, post_id, tmp_dir)
        image_files.append(title_img)

    # ---------- 3. Build slideshow + audio ----------
    try:
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        per_slide = total_duration / max(1, len(image_files))

        clips = []
        for img in image_files:
            clip = ImageClip(img).resize(width=1080).set_duration(per_slide)
            clips.append(clip)

        video = concatenate_videoclips(clips, method="compose").set_audio(audio_clip)

        video_filename = os.path.join(tmp_dir, f"video_{post_id}.mp4")

        # keep encoding simple & fast
        video.write_videofile(
            video_filename,
            fps=24,
            codec="libx264",
            audio_codec="aac"
        )
    except Exception as e:
        print("Video creation error:", e)
        return jsonify({"error": f"Video creation failed: {e}"}), 500
    finally:
        try:
            audio_clip.close()
        except Exception:
            pass

    # ---------- 4. Upload video to WordPress ----------
    wp_url = upload_to_wordpress(video_filename)
    print("Uploaded video URL:", wp_url)

    # ---------- 5. Cleanup ----------
    try:
        if os.path.exists(audio_path):
            os.remove(audio_path)
        for img in image_files:
            if os.path.exists(img):
                os.remove(img)
        if os.path.exists(video_filename):
            os.remove(video_filename)
    except Exception as e:
        print("Cleanup error:", e)

    return jsonify({"video_url": wp_url})


def upload_to_wordpress(file_path: str) -> str:
    """
    Upload the MP4 to WordPress media library via REST API
    and return the public URL.
    """
    if not (WP_BASE_URL and WP_USER and WP_APP_PASSWORD):
        print("Missing WP config")
        return ""

    creds = base64.b64encode(f"{WP_USER}:{WP_APP_PASSWORD}".encode("utf-8")).decode("utf-8")

    url = f"{WP_BASE_URL}/wp-json/wp/v2/media"

    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Disposition": f"attachment; filename={os.path.basename(file_path)}"
    }

    with open(file_path, "rb") as f:
        resp = requests.post(url, headers=headers, data=f)

    try:
        data = resp.json()
        print("WP upload response:", data)
    except Exception:
        print("WP upload error:", resp.text)
        return ""

    return data.get("source_url", "")


def create_title_image(title: str, post_id: str, tmp_dir: str) -> str:
    """
    Create a simple image with the blog title if no images are available.
    """
    width, height = 1080, 72
