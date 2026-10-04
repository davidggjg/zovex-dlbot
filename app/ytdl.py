"""Wrappers around yt-dlp. All blocking calls are meant to run in a worker thread."""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yt_dlp

from . import config
from .util import safe_filename

COMMON_HEIGHTS = [2160, 1440, 1080, 720, 480, 360, 240]


class DownloadError(Exception):
    pass


def cookie_file_for(url: str) -> str | None:
    """Return a cookies.txt matching the host, if the admin uploaded one."""
    host_map = {
        "instagram": ("instagram.com",),
        "tiktok": ("tiktok.com",),
        "youtube": ("youtube.com", "youtu.be"),
        "facebook": ("facebook.com", "fb.watch"),
        "twitter": ("twitter.com", "x.com"),
        "reddit": ("reddit.com",),
    }
    low = url.lower()
    for name, hosts in host_map.items():
        if any(h in low for h in hosts):
            path = config.COOKIES_DIR / f"{name}.txt"
            if path.exists():
                return str(path)
    generic = config.COOKIES_DIR / "cookies.txt"
    return str(generic) if generic.exists() else None


def base_opts(url: str) -> dict:
    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": not config.ALLOW_PLAYLISTS,
        "ignoreconfig": True,
        "socket_timeout": 30,
        "retries": 5,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "geo_bypass": True,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "trim_file_name": 80,
    }
    cookies = cookie_file_for(url)
    if cookies:
        opts["cookiefile"] = cookies
    return opts


@dataclass
class MediaInfo:
    url: str
    title: str
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    extractor: str
    is_live: bool
    heights: list[int] = field(default_factory=list)
    size_by_height: dict[int, int] = field(default_factory=dict)
    audio_size: int | None = None
    manual_subs: list[str] = field(default_factory=list)
    auto_subs: list[str] = field(default_factory=list)
    webpage_url: str = ""


def extract_info(url: str) -> MediaInfo:
    opts = base_opts(url) | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_clean_error(str(exc))) from exc

    if info.get("_type") == "playlist":
        entries = [e for e in (info.get("entries") or []) if e]
        if not entries:
            raise DownloadError("לא נמצא מדיה בקישור הזה.")
        info = entries[0]

    formats = info.get("formats") or []
    heights: set[int] = set()
    size_by_height: dict[int, int] = {}
    audio_size: int | None = None

    best_audio = 0
    for f in formats:
        if f.get("vcodec") in (None, "none") and f.get("acodec") not in (None, "none"):
            size = f.get("filesize") or f.get("filesize_approx") or 0
            best_audio = max(best_audio, size)
    audio_size = best_audio or None

    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        bucket = min((c for c in COMMON_HEIGHTS if h <= c + 40), default=None)
        if bucket is None:
            continue
        heights.add(bucket)
        size = f.get("filesize") or f.get("filesize_approx") or 0
        if size:
            total = size + (audio_size or 0 if f.get("acodec") in (None, "none") else 0)
            size_by_height[bucket] = max(size_by_height.get(bucket, 0), total)

    return MediaInfo(
        url=url,
        title=info.get("title") or "ללא שם",
        uploader=info.get("uploader") or info.get("channel"),
        duration=info.get("duration"),
        thumbnail=info.get("thumbnail"),
        extractor=info.get("extractor_key") or info.get("extractor") or "?",
        is_live=bool(info.get("is_live")),
        heights=sorted(heights, reverse=True),
        size_by_height=size_by_height,
        audio_size=audio_size,
        manual_subs=sorted((info.get("subtitles") or {}).keys()),
        auto_subs=sorted((info.get("automatic_captions") or {}).keys()),
        webpage_url=info.get("webpage_url") or url,
    )


def _clean_error(text: str) -> str:
    text = text.replace("ERROR: ", "").strip()
    low = text.lower()
    if "sign in to confirm" in low or "cookies" in low or "login required" in low:
        return (
            "הקישור דורש התחברות. צריך קובץ cookies — אדמין יכול להעלות אותו "
            "עם הפקודה /cookies."
        )
    if "private" in low:
        return "התוכן פרטי ולא נגיש."
    if "unavailable" in low or "removed" in low:
        return "התוכן לא קיים או הוסר."
    if "unsupported url" in low or "no suitable" in low:
        return "האתר הזה לא נתמך."
    if "geo" in low and "restrict" in low:
        return "התוכן חסום גאוגרפית לשרת הזה."
    return text[:400]


ProgressCb = Callable[[dict], None]


def _make_hook(cb: ProgressCb | None) -> Callable[[dict], None]:
    state = {"last": 0.0}

    def hook(d: dict) -> None:
        if cb is None:
            return
        now = time.monotonic()
        if d.get("status") == "downloading":
            if now - state["last"] < config.PROGRESS_INTERVAL:
                return
            state["last"] = now
        elif d.get("status") != "finished":
            return
        total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        cb(
            {
                "status": d.get("status"),
                "downloaded": d.get("downloaded_bytes") or 0,
                "total": total,
                "speed": d.get("speed") or 0,
                "eta": d.get("eta") or 0,
            }
        )

    return hook


def _format_selector(height: int | None) -> str:
    if height is None:
        return "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b"
    return (
        f"bv*[height<={height}][ext=mp4]+ba[ext=m4a]/"
        f"bv*[height<={height}]+ba/"
        f"b[height<={height}]/b"
    )


def download_video(
    url: str,
    workdir: Path,
    height: int | None,
    progress: ProgressCb | None = None,
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    opts = base_opts(url) | {
        "format": _format_selector(height),
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / "%(title).80s.%(ext)s"),
        "progress_hooks": [_make_hook(progress)],
        "max_filesize": config.MAX_FILESIZE,
        "postprocessors": [
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"},
        ],
    }
    return _run(opts, url, workdir)


def download_audio(
    url: str, workdir: Path, fmt: str = "mp3", progress: ProgressCb | None = None
) -> Path:
    workdir.mkdir(parents=True, exist_ok=True)
    pps: list[dict] = [
        {"key": "FFmpegExtractAudio", "preferredcodec": fmt, "preferredquality": "0"},
        {"key": "FFmpegMetadata"},
    ]
    if fmt == "mp3":
        pps.append({"key": "EmbedThumbnail"})
    opts = base_opts(url) | {
        "format": "ba/b",
        "outtmpl": str(workdir / "%(title).80s.%(ext)s"),
        "progress_hooks": [_make_hook(progress)],
        "max_filesize": config.MAX_FILESIZE,
        "writethumbnail": fmt == "mp3",
        "postprocessors": pps,
    }
    return _run(opts, url, workdir, want_ext=fmt)


def download_subtitles(
    url: str, workdir: Path, langs: list[str], auto: bool, fmt: str = "srt"
) -> list[Path]:
    workdir.mkdir(parents=True, exist_ok=True)
    opts = base_opts(url) | {
        "skip_download": True,
        "writesubtitles": not auto,
        "writeautomaticsub": auto,
        "subtitleslangs": langs,
        "subtitlesformat": "vtt/srt/best",
        "convertsubtitles": fmt,
        "outtmpl": str(workdir / "%(title).80s.%(ext)s"),
        "postprocessors": [{"key": "FFmpegSubtitlesConvertor", "format": fmt}],
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_clean_error(str(exc))) from exc
    files = sorted(Path(p) for p in glob.glob(str(workdir / f"*.{fmt}")))
    if not files:
        raise DownloadError("לא הצלחתי להוריד את קובץ הכתוביות.")
    return files


def _run(opts: dict, url: str, workdir: Path, want_ext: str | None = None) -> Path:
    before = set(glob.glob(str(workdir / "*")))
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "File is larger than max-filesize" in msg:
            raise DownloadError(
                f"הקובץ גדול מהמותר ({config.MAX_FILESIZE_MB}MB). נסה איכות נמוכה יותר."
            ) from exc
        raise DownloadError(_clean_error(msg)) from exc

    candidates = [
        Path(p)
        for p in glob.glob(str(workdir / "*"))
        if p not in before and not p.endswith((".part", ".ytdl", ".webp", ".jpg", ".png"))
    ]
    if want_ext:
        exact = [p for p in candidates if p.suffix.lstrip(".") == want_ext]
        if exact:
            candidates = exact
    if not candidates:
        requested = info.get("requested_downloads") or []
        if requested and requested[0].get("filepath"):
            candidates = [Path(requested[0]["filepath"])]
    if not candidates:
        raise DownloadError("ההורדה הסתיימה אבל לא נמצא קובץ פלט.")
    return max(candidates, key=lambda p: p.stat().st_size)


def mux_subtitles(video: Path, subs: list[Path], burn: bool) -> Path:
    """Soft-mux (fast, copy) or burn-in (re-encode) subtitles into the video."""
    out = video.with_name(safe_filename(video.stem) + (".hard.mp4" if burn else ".subbed.mp4"))
    if burn:
        sub = subs[0]
        # ffmpeg's subtitles filter needs escaped colons/commas in the path
        escaped = str(sub).replace("\\", "/").replace(":", r"\:").replace(",", r"\,")
        cmd = [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vf", f"subtitles='{escaped}':force_style='FontSize=22'",
            "-c:a", "copy", "-preset", "veryfast", "-crf", "23",
            str(out),
        ]
    else:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video)]
        for s in subs:
            cmd += ["-i", str(s)]
        cmd += ["-map", "0"]
        for i in range(len(subs)):
            cmd += ["-map", str(i + 1)]
        cmd += ["-c", "copy", "-c:s", "mov_text"]
        for i, s in enumerate(subs):
            lang = s.stem.split(".")[-1][:3] or "und"
            cmd += [f"-metadata:s:s:{i}", f"language={lang}"]
        cmd += [str(out)]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60 * 60 * 3)
    if proc.returncode != 0 or not out.exists():
        raise DownloadError("ffmpeg נכשל בשילוב הכתוביות: " + proc.stderr[-300:])
    return out


def cleanup(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def probe_dimensions(path: Path) -> tuple[int, int, int]:
    """(width, height, duration_seconds) via ffprobe; zeros if unavailable."""
    if not shutil.which("ffprobe"):
        return (0, 0, 0)
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
            ],
            capture_output=True, text=True, timeout=60,
        ).stdout.split()
        w, h, d = int(out[0]), int(out[1]), int(float(out[2]))
        return (w, h, d)
    except Exception:
        return (0, 0, 0)
