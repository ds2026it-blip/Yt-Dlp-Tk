#!/usr/bin/env python3
"""
yt_dlp_gui.py — Modern Desktop GUI for yt-dlp
Built with CustomTkinter, Pillow, and yt-dlp Python API.

Features:
- Fetch Video / Playlist Metadata (Thumbnail, Title, Channel, Duration, Views)
- Dynamic Format & Quality Selector (Best, 4K, 1440p, 1080p, 720p, 480p, Audio MP3/M4A/FLAC/WAV)
- Container Output Selection (MP4, MKV, WEBM)
- Subtitle / Auto-Caption embedding, SponsorBlock, & Thumbnail / Metadata embedding
- Playlist support (Full Playlist or Custom Item Range)
- Download Queue Manager with real-time progress, speed, ETA, Retry, Play File, and Cancellation
- Persistent user preferences (~/.yt_dlp_studio_config.json)
- Live Console Log drawer with yt-dlp logger integration
"""

from __future__ import annotations

import ctypes
import io
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Optional

# Ensure stdout/stderr are valid streams when launched via pythonw.exe (windowless mode)
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8", errors="ignore")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8", errors="ignore")

import customtkinter as ctk
import requests
import yt_dlp
from PIL import Image

# ─────────────────────────────────────────────────────────────
#  Theme & Appearance Configuration
# ─────────────────────────────────────────────────────────────

ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# Dual-mode Color Palette: (Light, Dark)
BG_APP = ("#f1f5f9", "#0b0e14")
CARD_BG = ("#ffffff", "#131722")
CARD_INNER = ("#f8fafc", "#0d1017")
CARD_BORDER = ("#e2e8f0", "#22293a")
INPUT_BORDER = ("#cbd5e1", "#2d364d")
BTN_SECONDARY = ("#e2e8f0", "#1e2536")
BTN_SECONDARY_HOVER = ("#cbd5e1", "#2d3748")

ACCENT_PRIMARY = "#3b82f6"
ACCENT_HOVER = "#2563eb"
SUCCESS_COLOR = "#10b981"
SUCCESS_HOVER = "#059669"
WARNING_COLOR = "#f59e0b"
DANGER_COLOR = "#ef4444"
DANGER_HOVER = "#dc2626"

TEXT_MAIN = ("#0f172a", "#f3f4f6")
TEXT_MUTED = ("#64748b", "#94a3b8")
BADGE_BG = ("#dbeafe", "#172554")
BADGE_TEXT = ("#1d4ed8", "#60a5fa")

ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
CONFIG_PATH = Path.home() / ".yt_dlp_studio_config.json"
APP_DIR = Path(__file__).resolve().parent
ICON_ICO_PATH = APP_DIR / "assets" / "icon.ico"
ICON_PNG_PATH = APP_DIR / "assets" / "icon.png"


# ─────────────────────────────────────────────────────────────
#  Helper Functions
# ─────────────────────────────────────────────────────────────

def strip_ansi(text: str) -> str:
    if not text:
        return ""
    return ANSI_ESCAPE_RE.sub("", text)


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None or seconds <= 0:
        return "Live / —"
    total_sec = int(seconds)
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def format_bytes(num_bytes: Optional[float]) -> str:
    if not num_bytes or num_bytes <= 0:
        return "—"
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} PB"


def format_count(count: Optional[int]) -> str:
    if count is None:
        return "—"
    if count >= 1_000_000_000:
        return f"{count / 1_000_000_000:.1f}B"
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def open_path_in_os(target_path: Path) -> None:
    """Open a file or directory using the platform's default handler."""
    if not target_path.exists():
        return
    if sys.platform == "win32":
        os.startfile(str(target_path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(target_path)])
    else:
        subprocess.Popen(["xdg-open", str(target_path)])


def find_media_player() -> Optional[str]:
    """Detect a media player capable of playing partial (.part) files and live streams."""
    for cmd in ("mpv", "vlc", "mpc-hc64", "mpc-hc", "potplayer", "potplayermini64", "ffplay"):
        found = shutil.which(cmd)
        if found:
            return found
    if sys.platform == "win32":
        win_candidates = [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "VideoLAN" / "VLC" / "vlc.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "VideoLAN" / "VLC" / "vlc.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MPC-HC" / "mpc-hc64.exe",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "DAUM" / "PotPlayer" / "PotPlayerMini64.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "mpv" / "mpv.exe",
        ]
        for candidate in win_candidates:
            if candidate.exists():
                return str(candidate)
    return None


# ─────────────────────────────────────────────────────────────
#  Data Models
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FormatPreset:
    label: str
    format_spec: str
    is_audio_only: bool = False
    audio_codec: str = ""
    audio_quality: str = "192"


@dataclass
class QueueTask:
    task_id: str
    url: str
    title: str
    thumbnail_img: Optional[ctk.CTkImage]
    preset: FormatPreset
    container: str
    output_dir: str
    embed_subs: bool = False
    embed_thumb: bool = False
    embed_metadata: bool = True
    sponsorblock: bool = False
    playlist_items: str = ""
    cookies_browser: str = "None"
    status: str = "Queued"  # Queued, Downloading, Paused, Processing, Completed, Cancelled, Error
    progress: float = 0.0
    speed_str: str = "—"
    eta_str: str = "—"
    size_str: str = "—"
    playlist_progress: str = ""
    output_file: str = ""
    part_file: str = ""
    error_msg: str = ""
    last_ui_ts: float = 0.0
    stop_reason: str = ""  # "pause" | "cancel"
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "url": self.url,
            "title": self.title,
            "preset": {
                "label": self.preset.label,
                "format_spec": self.preset.format_spec,
                "is_audio_only": self.preset.is_audio_only,
                "audio_codec": self.preset.audio_codec,
                "audio_quality": self.preset.audio_quality,
            },
            "container": self.container,
            "output_dir": self.output_dir,
            "embed_subs": self.embed_subs,
            "embed_thumb": self.embed_thumb,
            "embed_metadata": self.embed_metadata,
            "sponsorblock": self.sponsorblock,
            "playlist_items": self.playlist_items,
            "cookies_browser": self.cookies_browser,
            "status": "Paused" if self.status in ("Downloading", "Processing", "Queued") else self.status,
            "progress": self.progress,
            "size_str": self.size_str,
            "playlist_progress": self.playlist_progress,
            "output_file": self.output_file,
            "part_file": self.part_file,
            "error_msg": self.error_msg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueTask":
        p_data = data.get("preset") or {}
        preset = FormatPreset(
            label=p_data.get("label", "🌟 Best Video + Audio (Auto)"),
            format_spec=p_data.get("format_spec", "bestvideo+bestaudio/best"),
            is_audio_only=bool(p_data.get("is_audio_only", False)),
            audio_codec=p_data.get("audio_codec", ""),
            audio_quality=p_data.get("audio_quality", "192"),
        )
        return cls(
            task_id=data.get("task_id", f"task_{int(time.time() * 1000)}"),
            url=data.get("url", ""),
            title=data.get("title", data.get("url", "")),
            thumbnail_img=None,
            preset=preset,
            container=data.get("container", "mp4"),
            output_dir=data.get("output_dir", str(Path.home() / "Downloads")),
            embed_subs=bool(data.get("embed_subs", False)),
            embed_thumb=bool(data.get("embed_thumb", False)),
            embed_metadata=bool(data.get("embed_metadata", True)),
            sponsorblock=bool(data.get("sponsorblock", False)),
            playlist_items=data.get("playlist_items", ""),
            cookies_browser=data.get("cookies_browser", "None"),
            status=data.get("status", "Paused"),
            progress=float(data.get("progress", 0.0)),
            size_str=data.get("size_str", "—"),
            playlist_progress=data.get("playlist_progress", ""),
            output_file=data.get("output_file", ""),
            part_file=data.get("part_file", ""),
            error_msg=data.get("error_msg", ""),
        )


class DownloadCancelledError(Exception):
    """Raised inside yt-dlp hooks to abort an active download cleanly."""


class _YtDlpLogger:
    """Forwards yt-dlp internal warnings/errors to the GUI log callback."""

    def __init__(self, log_fn: Callable[[str], None]) -> None:
        self._log = log_fn

    def debug(self, msg: str) -> None:
        # Only forward post-processor / merger status lines, ignore noisy debug lines
        if msg.startswith("[Merger]") or msg.startswith("[ExtractAudio]") or msg.startswith("[EmbedSubtitle]"):
            self._log(msg)

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        self._log(f"WARN: {msg}")

    def error(self, msg: str) -> None:
        self._log(f"ERROR: {msg}")


# ─────────────────────────────────────────────────────────────
#  Queue Item Widget
# ─────────────────────────────────────────────────────────────

class QueueItemCard(ctk.CTkFrame):
    """Visual card representing a single download task in the queue."""

    def __init__(
        self,
        master: Any,
        task: QueueTask,
        on_move_up: Callable[[QueueTask], None],
        on_move_down: Callable[[QueueTask], None],
        on_pause: Callable[[QueueTask], None],
        on_resume: Callable[[QueueTask], None],
        on_cancel: Callable[[QueueTask], None],
        on_remove: Callable[[QueueTask], None],
        on_open_folder: Callable[[QueueTask], None],
        on_play_file: Callable[[QueueTask], None],
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            fg_color=CARD_BG,
            corner_radius=10,
            border_width=1,
            border_color=CARD_BORDER,
            **kwargs,
        )
        self.task = task
        self.on_move_up = on_move_up
        self.on_move_down = on_move_down
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_cancel = on_cancel
        self.on_remove = on_remove
        self.on_open_folder = on_open_folder
        self.on_play_file = on_play_file

        self.grid_columnconfigure(2, weight=1)

        # Sequence Reorder Controls (Up / Position / Down)
        self.order_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.order_frame.grid(row=0, column=0, rowspan=3, padx=(10, 2), pady=8, sticky="ns")

        self.up_btn = ctk.CTkButton(
            self.order_frame,
            text="▲",
            width=24,
            height=20,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda: self.on_move_up(self.task),
        )
        self.up_btn.pack(side="top", pady=(0, 2))

        self.pos_label = ctk.CTkLabel(
            self.order_frame,
            text="#1",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=TEXT_MUTED,
        )
        self.pos_label.pack(side="top", pady=1)

        self.down_btn = ctk.CTkButton(
            self.order_frame,
            text="▼",
            width=24,
            height=20,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=10, weight="bold"),
            command=lambda: self.on_move_down(self.task),
        )
        self.down_btn.pack(side="top", pady=(2, 0))

        # Thumbnail or Icon
        self.thumb_label = ctk.CTkLabel(
            self,
            text="🎬" if not task.preset.is_audio_only else "🎵",
            width=92,
            height=52,
            fg_color=CARD_INNER,
            corner_radius=6,
            font=ctk.CTkFont(size=20),
        )
        if task.thumbnail_img:
            self.thumb_label.configure(image=task.thumbnail_img, text="")
        self.thumb_label.grid(row=0, column=1, rowspan=3, padx=(6, 12), pady=10, sticky="ns")

        # Title + Tag Row
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.grid(row=0, column=2, sticky="ew", padx=(0, 8), pady=(10, 2))
        top_frame.grid_columnconfigure(0, weight=1)

        self.title_label = ctk.CTkLabel(
            top_frame,
            text=task.title or task.url,
            font=ctk.CTkFont(size=13, weight="bold"),
            text_color=TEXT_MAIN,
            anchor="w",
        )
        self.title_label.grid(row=0, column=0, sticky="ew")

        badge_text = (
            f"{task.preset.label} • {task.preset.audio_codec.upper()}"
            if task.preset.is_audio_only
            else f"{task.preset.label} • {task.container.upper()}"
        )
        self.badge_label = ctk.CTkLabel(
            top_frame,
            text=badge_text,
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=BADGE_TEXT,
            fg_color=BADGE_BG,
            corner_radius=6,
            padx=8,
            pady=2,
        )
        self.badge_label.grid(row=0, column=1, padx=(8, 0))

        # Progress bar
        self.progress_bar = ctk.CTkProgressBar(
            self,
            height=7,
            corner_radius=4,
            progress_color=ACCENT_PRIMARY,
            fg_color=BTN_SECONDARY,
        )
        self.progress_bar.set(0.0)
        self.progress_bar.grid(row=1, column=2, sticky="ew", padx=(0, 8), pady=4)

        # Stats Row
        self.stats_label = ctk.CTkLabel(
            self,
            text="⏳ Queued — waiting to start",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.stats_label.grid(row=2, column=2, sticky="ew", padx=(0, 8), pady=(0, 10))

        # Action Buttons Frame
        self.btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.btn_frame.grid(row=0, column=3, rowspan=3, padx=(4, 12), pady=10, sticky="e")

        self.play_btn = ctk.CTkButton(
            self.btn_frame,
            text="▶",
            width=32,
            height=30,
            fg_color=SUCCESS_COLOR,
            hover_color=SUCCESS_HOVER,
            text_color="white",
            command=lambda: self.on_play_file(self.task),
        )
        # Hidden until completed

        self.pause_resume_btn = ctk.CTkButton(
            self.btn_frame,
            text="⏸",
            width=32,
            height=30,
            fg_color=WARNING_COLOR,
            hover_color="#d97706",
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._handle_pause_resume,
        )

        self.folder_btn = ctk.CTkButton(
            self.btn_frame,
            text="📂",
            width=32,
            height=30,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            command=lambda: self.on_open_folder(self.task),
        )
        self.folder_btn.pack(side="left", padx=3)

        self.action_btn = ctk.CTkButton(
            self.btn_frame,
            text="✕",
            width=32,
            height=30,
            fg_color=DANGER_COLOR,
            hover_color=DANGER_HOVER,
            text_color="white",
            command=self._handle_action,
        )
        self.action_btn.pack(side="left", padx=3)

    def _handle_pause_resume(self) -> None:
        if self.task.status in ("Queued", "Downloading"):
            self.on_pause(self.task)
        elif self.task.status in ("Paused", "Cancelled", "Error"):
            self.on_resume(self.task)

    def _handle_action(self) -> None:
        if self.task.status in ("Queued", "Downloading", "Processing"):
            self.on_cancel(self.task)
        else:
            self.on_remove(self.task)

    def set_order_state(self, index: int, total: int) -> None:
        self.pos_label.configure(text=f"#{index + 1}")
        self.up_btn.configure(state="normal" if index > 0 else "disabled")
        self.down_btn.configure(state="normal" if index < total - 1 else "disabled")

    def refresh_ui(self) -> None:
        t = self.task
        self.title_label.configure(text=t.title or t.url)
        self.progress_bar.set(max(0.0, min(1.0, t.progress)))

        pl_prefix = f"[{t.playlist_progress}] " if t.playlist_progress else ""

        # Reset conditional buttons
        self.play_btn.pack_forget()
        self.pause_resume_btn.pack_forget()

        if t.status == "Queued":
            self.stats_label.configure(
                text=f"{pl_prefix}⏳ Queued — waiting for worker slot",
                text_color=TEXT_MUTED,
            )
            self.progress_bar.configure(progress_color=ACCENT_PRIMARY)
            self.pause_resume_btn.configure(
                text="⏸",
                fg_color=WARNING_COLOR,
                hover_color="#d97706",
            )
            self.pause_resume_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="✕",
                fg_color=DANGER_COLOR,
                hover_color=DANGER_HOVER,
                text_color="white",
            )

        elif t.status == "Downloading":
            pct = f"{t.progress * 100:.1f}%"
            stats = (
                f"{pl_prefix}⬇ Downloading: {pct}   •   "
                f"Size: {t.size_str}   •   Speed: {t.speed_str}   •   ETA: {t.eta_str}"
            )
            self.stats_label.configure(text=stats, text_color=TEXT_MAIN)
            self.progress_bar.configure(progress_color=ACCENT_PRIMARY)
            if t.part_file or t.progress > 0.01:
                self.play_btn.configure(
                    text="👁",
                    fg_color="#0ea5e9",
                    hover_color="#0284c7",
                )
                self.play_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.pause_resume_btn.configure(
                text="⏸",
                fg_color=WARNING_COLOR,
                hover_color="#d97706",
            )
            self.pause_resume_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="⏹",
                fg_color=DANGER_COLOR,
                hover_color=DANGER_HOVER,
                text_color="white",
            )

        elif t.status == "Paused":
            pct = f"{t.progress * 100:.1f}%"
            size_info = f" ({t.size_str})" if t.size_str and t.size_str != "—" else ""
            self.progress_bar.configure(progress_color=WARNING_COLOR)
            self.stats_label.configure(
                text=f"{pl_prefix}⏸ Paused at {pct}{size_info} — click ▶ to resume or 👁 to preview",
                text_color=WARNING_COLOR,
            )
            if t.part_file or t.progress > 0.01:
                self.play_btn.configure(
                    text="👁",
                    fg_color="#0ea5e9",
                    hover_color="#0284c7",
                )
                self.play_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.pause_resume_btn.configure(
                text="▶",
                fg_color=ACCENT_PRIMARY,
                hover_color=ACCENT_HOVER,
            )
            self.pause_resume_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="🗑",
                fg_color=BTN_SECONDARY,
                hover_color=BTN_SECONDARY_HOVER,
                text_color=TEXT_MAIN,
            )

        elif t.status == "Processing":
            self.progress_bar.set(1.0)
            self.progress_bar.configure(progress_color=WARNING_COLOR)
            self.stats_label.configure(
                text=f"{pl_prefix}⚙ Post-processing (Merging streams / Embedding metadata)...",
                text_color=WARNING_COLOR,
            )

        elif t.status == "Completed":
            self.progress_bar.set(1.0)
            self.progress_bar.configure(progress_color=SUCCESS_COLOR)
            fname = Path(t.output_file).name if t.output_file else "Finished"
            self.stats_label.configure(
                text=f"{pl_prefix}✔ Completed — {fname} ({t.size_str})",
                text_color=SUCCESS_COLOR,
            )
            if t.output_file and Path(t.output_file).exists():
                self.play_btn.configure(
                    text="▶",
                    fg_color=SUCCESS_COLOR,
                    hover_color=SUCCESS_HOVER,
                )
                self.play_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="🗑",
                fg_color=BTN_SECONDARY,
                hover_color=BTN_SECONDARY_HOVER,
                text_color=TEXT_MAIN,
            )

        elif t.status == "Cancelled":
            self.progress_bar.configure(progress_color=WARNING_COLOR)
            self.stats_label.configure(
                text="⏹ Stopped — click ▶ to resume or 👁 to preview partial file",
                text_color=WARNING_COLOR,
            )
            if t.part_file or t.progress > 0.01:
                self.play_btn.configure(
                    text="👁",
                    fg_color="#0ea5e9",
                    hover_color="#0284c7",
                )
                self.play_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.pause_resume_btn.configure(
                text="▶",
                fg_color=ACCENT_PRIMARY,
                hover_color=ACCENT_HOVER,
            )
            self.pause_resume_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="🗑",
                fg_color=BTN_SECONDARY,
                hover_color=BTN_SECONDARY_HOVER,
                text_color=TEXT_MAIN,
            )

        elif t.status == "Error":
            self.progress_bar.configure(progress_color=DANGER_COLOR)
            err_short = t.error_msg[:95] + ("..." if len(t.error_msg) > 95 else "")
            self.stats_label.configure(text=f"✖ Error: {err_short}", text_color=DANGER_COLOR)
            self.pause_resume_btn.configure(
                text="↻",
                fg_color=ACCENT_PRIMARY,
                hover_color=ACCENT_HOVER,
            )
            self.pause_resume_btn.pack(side="left", padx=3, before=self.folder_btn)
            self.action_btn.configure(
                text="🗑",
                fg_color=BTN_SECONDARY,
                hover_color=BTN_SECONDARY_HOVER,
                text_color=TEXT_MAIN,
            )


# ─────────────────────────────────────────────────────────────
#  Main GUI Application
# ─────────────────────────────────────────────────────────────

class YtDlpGuiApp(ctk.CTk):
    def __init__(self) -> None:
        if sys.platform == "win32":
            try:
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ytdlp.studio.gui.1")
            except Exception:
                pass

        super().__init__()

        self.title("yt-dlp Studio — Video & Audio Downloader")
        self.geometry("1060x800")
        self.minsize(920, 680)
        self.configure(fg_color=BG_APP)
        self._apply_window_icon()
        self.after(200, self._apply_window_icon)

        # State
        self.current_info: Optional[dict[str, Any]] = None
        self.current_thumb_ctk_large: Optional[ctk.CTkImage] = None
        self.current_thumb_ctk_small: Optional[ctk.CTkImage] = None
        self.available_presets: list[FormatPreset] = self._default_presets()
        self._analyze_req_id: int = 0

        self.tasks: list[QueueTask] = []
        self.task_cards: dict[str, QueueItemCard] = {}
        self.active_workers = 0
        self.max_concurrent = 2
        self.queue_lock = threading.Lock()
        self._http_session = requests.Session()
        self._http_session.headers.update({"User-Agent": "Mozilla/5.0"})

        self.default_download_dir = str(Path.home() / "Downloads")
        if not Path(self.default_download_dir).exists():
            self.default_download_dir = str(Path.cwd())

        self.prefs = self._load_prefs()

        self._build_ui()
        self._apply_saved_prefs()
        self._check_dependencies()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ─────────────────────────────────────────────────────────
    #  Preferences Persistence
    # ─────────────────────────────────────────────────────────

    def _load_prefs(self) -> dict[str, Any]:
        if CONFIG_PATH.exists():
            try:
                return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {}

    def _save_prefs(self) -> None:
        persisted_queue = [
            t.to_dict()
            for t in self.tasks
            if t.status in ("Queued", "Downloading", "Paused", "Cancelled", "Error")
        ]
        data = {
            "output_dir": self.path_entry.get().strip() or self.default_download_dir,
            "container": self.container_menu.get(),
            "embed_subs": self.sub_var.get(),
            "embed_thumb": self.thumb_var.get(),
            "embed_metadata": self.meta_var.get(),
            "sponsorblock": self.sb_var.get(),
            "cookies_browser": self.cookies_menu.get(),
            "max_concurrent": self.max_concurrent,
            "appearance_mode": self.theme_switch.get(),
            "saved_queue": persisted_queue,
        }
        try:
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _apply_saved_prefs(self) -> None:
        if not self.prefs:
            return
        saved_dir = self.prefs.get("output_dir")
        if saved_dir and Path(saved_dir).exists():
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, saved_dir)
        if self.prefs.get("container") in ("mp4", "mkv", "webm"):
            self.container_menu.set(self.prefs["container"])
        if "embed_subs" in self.prefs:
            self.sub_var.set(bool(self.prefs["embed_subs"]))
        if "embed_thumb" in self.prefs:
            self.thumb_var.set(bool(self.prefs["embed_thumb"]))
        if "embed_metadata" in self.prefs:
            self.meta_var.set(bool(self.prefs["embed_metadata"]))
        if "sponsorblock" in self.prefs:
            self.sb_var.set(bool(self.prefs["sponsorblock"]))
        if self.prefs.get("cookies_browser") in ("None", "chrome", "edge", "firefox", "brave", "opera"):
            self.cookies_menu.set(self.prefs["cookies_browser"])
        if self.prefs.get("max_concurrent") in (1, 2, 3, 4):
            self.max_concurrent = int(self.prefs["max_concurrent"])
            self.concurrent_menu.set(f"{self.max_concurrent}x")
        mode = self.prefs.get("appearance_mode")
        if mode in ("Dark", "Light", "System"):
            self.theme_switch.set(mode)
            ctk.set_appearance_mode(mode)

        saved_queue = self.prefs.get("saved_queue")
        if isinstance(saved_queue, list) and saved_queue:
            for item_data in saved_queue:
                if isinstance(item_data, dict) and item_data.get("url"):
                    task = QueueTask.from_dict(item_data)
                    self._add_task_card(task)
            self._update_queue_header()
            self.log(f"Restored {len(self.tasks)} unfinished task(s) from previous session (click ▶ to resume).")

    def _on_close(self) -> None:
        active = [t for t in self.tasks if t.status in ("Downloading", "Processing")]
        if active:
            if not messagebox.askyesno(
                "Active Downloads",
                f"There are {len(active)} download(s) currently in progress.\n"
                "Pause them (so you can resume next time) and exit?",
            ):
                return
            for t in active:
                t.stop_reason = "pause"
                t.cancel_event.set()
        self._save_prefs()
        self.destroy()

    # ─────────────────────────────────────────────────────────
    #  Presets & Formats
    # ─────────────────────────────────────────────────────────

    def _default_presets(self) -> list[FormatPreset]:
        return [
            FormatPreset("🌟 Best Video + Audio (Auto)", "bestvideo+bestaudio/best"),
            FormatPreset("🎬 4K Ultra HD (2160p)", "bestvideo[height<=2160]+bestaudio/best[height<=2160]"),
            FormatPreset("🎬 2K QHD (1440p)", "bestvideo[height<=1440]+bestaudio/best[height<=1440]"),
            FormatPreset("🎬 Full HD (1080p)", "bestvideo[height<=1080]+bestaudio/best[height<=1080]"),
            FormatPreset("🎬 HD (720p)", "bestvideo[height<=720]+bestaudio/best[height<=720]"),
            FormatPreset("🎬 SD (480p)", "bestvideo[height<=480]+bestaudio/best[height<=480]"),
            FormatPreset("🎬 Low (360p)", "bestvideo[height<=360]+bestaudio/best[height<=360]"),
            FormatPreset("🎵 Audio Only — MP3 (320kbps)", "bestaudio/best", is_audio_only=True, audio_codec="mp3", audio_quality="320"),
            FormatPreset("🎵 Audio Only — MP3 (192kbps)", "bestaudio/best", is_audio_only=True, audio_codec="mp3", audio_quality="192"),
            FormatPreset("🎵 Audio Only — M4A (AAC)", "bestaudio[ext=m4a]/bestaudio/best", is_audio_only=True, audio_codec="m4a", audio_quality="192"),
            FormatPreset("🎵 Audio Only — FLAC (Lossless)", "bestaudio/best", is_audio_only=True, audio_codec="flac", audio_quality="0"),
            FormatPreset("🎵 Audio Only — WAV", "bestaudio/best", is_audio_only=True, audio_codec="wav", audio_quality="0"),
        ]

    # ─────────────────────────────────────────────────────────
    #  UI Construction
    # ─────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # 1. Header Bar
        header = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=0,
            height=58,
            border_width=1,
            border_color=CARD_BORDER,
        )
        header.grid(row=0, column=0, sticky="ew")
        header.grid_columnconfigure(1, weight=1)

        brand_frame = ctk.CTkFrame(header, fg_color="transparent")
        brand_frame.grid(row=0, column=0, padx=20, pady=12, sticky="w")

        if ICON_PNG_PATH.exists():
            try:
                logo_pil = Image.open(ICON_PNG_PATH).convert("RGBA")
                self._brand_icon_ctk = ctk.CTkImage(light_image=logo_pil, dark_image=logo_pil, size=(30, 30))
                icon_lbl = ctk.CTkLabel(brand_frame, text="", image=self._brand_icon_ctk)
                icon_lbl.pack(side="left", padx=(0, 8))
            except Exception:
                pass

        logo_badge = ctk.CTkLabel(
            brand_frame,
            text="yt-dlp",
            font=ctk.CTkFont(size=15, weight="bold"),
            fg_color=ACCENT_PRIMARY,
            text_color="white",
            corner_radius=6,
            padx=10,
            pady=4,
        )
        logo_badge.pack(side="left", padx=(0, 8))

        title_lbl = ctk.CTkLabel(
            brand_frame,
            text="Studio",
            font=ctk.CTkFont(size=18, weight="bold"),
            text_color=TEXT_MAIN,
        )
        title_lbl.pack(side="left")

        ver_lbl = ctk.CTkLabel(
            brand_frame,
            text=f"core {yt_dlp.version.__version__}",
            font=ctk.CTkFont(size=11),
            text_color=BADGE_TEXT,
            fg_color=BADGE_BG,
            corner_radius=5,
            padx=7,
            pady=1,
        )
        ver_lbl.pack(side="left", padx=(10, 0))

        # Right Header Status
        right_header = ctk.CTkFrame(header, fg_color="transparent")
        right_header.grid(row=0, column=2, padx=20, pady=12, sticky="e")

        self.ffmpeg_badge = ctk.CTkLabel(
            right_header,
            text="FFmpeg: Checking...",
            font=ctk.CTkFont(size=11, weight="bold"),
            fg_color=BTN_SECONDARY,
            corner_radius=6,
            padx=10,
            pady=4,
        )
        self.ffmpeg_badge.pack(side="left", padx=(0, 10))

        self.theme_switch = ctk.CTkOptionMenu(
            right_header,
            values=["Dark", "Light", "System"],
            width=95,
            height=28,
            fg_color=BTN_SECONDARY,
            button_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            command=self._on_theme_change,
        )
        self.theme_switch.set("Dark")
        self.theme_switch.pack(side="left")

        # 2. URL Input Card
        url_card = ctk.CTkFrame(
            self,
            fg_color=CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=CARD_BORDER,
        )
        url_card.grid(row=1, column=0, padx=20, pady=(14, 8), sticky="ew")
        url_card.grid_columnconfigure(0, weight=1)

        self.url_entry = ctk.CTkEntry(
            url_card,
            placeholder_text="Paste YouTube, TikTok, Instagram, X/Twitter, Twitch, SoundCloud, or direct video/playlist URL...",
            height=42,
            font=ctk.CTkFont(size=13),
            fg_color=CARD_INNER,
            border_color=INPUT_BORDER,
            text_color=TEXT_MAIN,
        )
        self.url_entry.grid(row=0, column=0, padx=(14, 6), pady=12, sticky="ew")
        self.url_entry.bind("<Return>", lambda _: self.fetch_metadata())

        clear_url_btn = ctk.CTkButton(
            url_card,
            text="✕",
            width=36,
            height=42,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self._clear_url,
        )
        clear_url_btn.grid(row=0, column=1, padx=3, pady=12)

        paste_btn = ctk.CTkButton(
            url_card,
            text="📋 Paste",
            width=82,
            height=42,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12, weight="bold"),
            command=self._paste_clipboard,
        )
        paste_btn.grid(row=0, column=2, padx=3, pady=12)

        self.analyze_btn = ctk.CTkButton(
            url_card,
            text="🔍 Analyze URL",
            width=132,
            height=42,
            fg_color=ACCENT_PRIMARY,
            hover_color=ACCENT_HOVER,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.fetch_metadata,
        )
        self.analyze_btn.grid(row=0, column=3, padx=(4, 14), pady=12)

        # 3. Middle Panel: Metadata Preview (Left) + Download Settings (Right)
        mid_frame = ctk.CTkFrame(self, fg_color="transparent")
        mid_frame.grid(row=2, column=0, padx=20, pady=6, sticky="ew")
        mid_frame.grid_columnconfigure(0, weight=5)
        mid_frame.grid_columnconfigure(1, weight=6)

        # 3A. Media Preview Card
        self.preview_card = ctk.CTkFrame(
            mid_frame,
            fg_color=CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self.preview_card.grid(row=0, column=0, padx=(0, 8), sticky="nsew")
        self.preview_card.grid_columnconfigure(1, weight=1)

        self.thumb_box = ctk.CTkLabel(
            self.preview_card,
            text="🎞️\nNo Media Loaded\n\nPaste a link & click Analyze\nor directly click Download",
            width=210,
            height=124,
            fg_color=CARD_INNER,
            corner_radius=8,
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        )
        self.thumb_box.grid(row=0, column=0, rowspan=4, padx=14, pady=14, sticky="n")

        self.info_title = ctk.CTkLabel(
            self.preview_card,
            text="Ready to Download",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=TEXT_MAIN,
            anchor="w",
            justify="left",
            wraplength=245,
        )
        self.info_title.grid(row=0, column=1, padx=(0, 14), pady=(14, 4), sticky="nw")

        self.info_uploader = ctk.CTkLabel(
            self.preview_card,
            text="Channel / Uploader: —",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.info_uploader.grid(row=1, column=1, padx=(0, 14), pady=2, sticky="nw")

        self.info_meta = ctk.CTkLabel(
            self.preview_card,
            text="Duration: —   •   Views: —",
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MUTED,
            anchor="w",
        )
        self.info_meta.grid(row=2, column=1, padx=(0, 14), pady=2, sticky="nw")

        self.info_badge = ctk.CTkLabel(
            self.preview_card,
            text="Supports 1000+ sites via yt-dlp",
            font=ctk.CTkFont(size=11, weight="bold"),
            text_color=ACCENT_PRIMARY,
            anchor="w",
            wraplength=245,
            justify="left",
        )
        self.info_badge.grid(row=3, column=1, padx=(0, 14), pady=(4, 14), sticky="sw")

        # 3B. Options & Format Card
        opts_card = ctk.CTkFrame(
            mid_frame,
            fg_color=CARD_BG,
            corner_radius=12,
            border_width=1,
            border_color=CARD_BORDER,
        )
        opts_card.grid(row=0, column=1, padx=(8, 0), sticky="nsew")
        opts_card.grid_columnconfigure(1, weight=1)

        # Quality / Format Row
        ctk.CTkLabel(
            opts_card,
            text="Format:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=(14, 8), pady=(12, 6), sticky="w")

        format_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        format_row.grid(row=0, column=1, padx=(0, 14), pady=(12, 6), sticky="ew")
        format_row.grid_columnconfigure(0, weight=1)

        self.format_menu = ctk.CTkOptionMenu(
            format_row,
            values=[p.label for p in self.available_presets],
            height=32,
            fg_color=CARD_INNER,
            button_color=BTN_SECONDARY,
            button_hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            command=self._on_preset_changed,
        )
        self.format_menu.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        self.container_menu = ctk.CTkOptionMenu(
            format_row,
            values=["mp4", "mkv", "webm"],
            width=85,
            height=32,
            fg_color=CARD_INNER,
            button_color=BTN_SECONDARY,
            button_hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
        )
        self.container_menu.set("mp4")
        self.container_menu.grid(row=0, column=1)

        # Save Path Row
        ctk.CTkLabel(
            opts_card,
            text="Save To:",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_MUTED,
        ).grid(row=1, column=0, padx=(14, 8), pady=6, sticky="w")

        path_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        path_row.grid(row=1, column=1, padx=(0, 14), pady=6, sticky="ew")
        path_row.grid_columnconfigure(0, weight=1)

        self.path_entry = ctk.CTkEntry(
            path_row,
            height=32,
            font=ctk.CTkFont(size=12),
            fg_color=CARD_INNER,
            border_color=INPUT_BORDER,
            text_color=TEXT_MAIN,
        )
        self.path_entry.insert(0, self.default_download_dir)
        self.path_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        browse_btn = ctk.CTkButton(
            path_row,
            text="📁 Browse",
            width=85,
            height=32,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=12),
            command=self._browse_folder,
        )
        browse_btn.grid(row=0, column=1)

        # Checkboxes & Extra Settings Row
        toggles_frame = ctk.CTkFrame(opts_card, fg_color="transparent")
        toggles_frame.grid(row=2, column=0, columnspan=2, padx=14, pady=6, sticky="ew")

        self.sub_var = ctk.BooleanVar(value=False)
        self.sub_cb = ctk.CTkCheckBox(
            toggles_frame,
            text="Subtitles",
            variable=self.sub_var,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MAIN,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.sub_cb.pack(side="left", padx=(0, 12))

        self.thumb_var = ctk.BooleanVar(value=False)
        self.thumb_cb = ctk.CTkCheckBox(
            toggles_frame,
            text="Thumbnail",
            variable=self.thumb_var,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MAIN,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.thumb_cb.pack(side="left", padx=(0, 12))

        self.meta_var = ctk.BooleanVar(value=True)
        self.meta_cb = ctk.CTkCheckBox(
            toggles_frame,
            text="Metadata",
            variable=self.meta_var,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MAIN,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.meta_cb.pack(side="left", padx=(0, 12))

        self.sb_var = ctk.BooleanVar(value=False)
        self.sb_cb = ctk.CTkCheckBox(
            toggles_frame,
            text="SponsorBlock",
            variable=self.sb_var,
            font=ctk.CTkFont(size=12),
            text_color=TEXT_MAIN,
            checkbox_width=18,
            checkbox_height=18,
        )
        self.sb_cb.pack(side="left")

        # Playlist items & Browser Cookies + Download Button Row
        action_row = ctk.CTkFrame(opts_card, fg_color="transparent")
        action_row.grid(row=3, column=0, columnspan=2, padx=14, pady=(6, 12), sticky="ew")
        action_row.grid_columnconfigure(3, weight=1)

        ctk.CTkLabel(
            action_row,
            text="Items:",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=0, padx=(0, 4), sticky="w")

        self.playlist_entry = ctk.CTkEntry(
            action_row,
            placeholder_text="All (e.g. 1-5)",
            width=92,
            height=34,
            font=ctk.CTkFont(size=11),
            fg_color=CARD_INNER,
            border_color=INPUT_BORDER,
            text_color=TEXT_MAIN,
        )
        self.playlist_entry.grid(row=0, column=1, padx=(0, 10))

        ctk.CTkLabel(
            action_row,
            text="Cookies:",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).grid(row=0, column=2, padx=(0, 4), sticky="w")

        self.cookies_menu = ctk.CTkOptionMenu(
            action_row,
            values=["None", "chrome", "edge", "firefox", "brave", "opera"],
            width=88,
            height=34,
            font=ctk.CTkFont(size=11),
            fg_color=CARD_INNER,
            button_color=BTN_SECONDARY,
            button_hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
        )
        self.cookies_menu.set("None")
        self.cookies_menu.grid(row=0, column=3, sticky="w")

        self.download_btn = ctk.CTkButton(
            action_row,
            text="⬇ Add & Download",
            height=36,
            width=155,
            fg_color=SUCCESS_COLOR,
            hover_color=SUCCESS_HOVER,
            text_color="white",
            font=ctk.CTkFont(size=13, weight="bold"),
            command=self.enqueue_current,
        )
        self.download_btn.grid(row=0, column=4, padx=(10, 0), sticky="e")

        # 4. Bottom Tabs: Download Queue & Console Logs
        self.tabs = ctk.CTkTabview(
            self,
            fg_color=CARD_BG,
            segmented_button_fg_color=CARD_INNER,
            segmented_button_selected_color=ACCENT_PRIMARY,
            segmented_button_unselected_color=CARD_INNER,
            text_color="white",
            corner_radius=12,
            border_width=1,
            border_color=CARD_BORDER,
        )
        self.tabs.grid(row=3, column=0, padx=20, pady=(4, 16), sticky="nsew")

        tab_queue = self.tabs.add("📥 Download Queue")
        tab_logs = self.tabs.add("🖥 Activity & yt-dlp Log")

        # Queue Tab Layout
        tab_queue.grid_columnconfigure(0, weight=1)
        tab_queue.grid_rowconfigure(1, weight=1)

        queue_toolbar = ctk.CTkFrame(tab_queue, fg_color="transparent")
        queue_toolbar.grid(row=0, column=0, sticky="ew", padx=6, pady=(0, 6))

        self.queue_summary_lbl = ctk.CTkLabel(
            queue_toolbar,
            text="No downloads in queue",
            font=ctk.CTkFont(size=12, weight="bold"),
            text_color=TEXT_MUTED,
        )
        self.queue_summary_lbl.pack(side="left", padx=4)

        clear_done_btn = ctk.CTkButton(
            queue_toolbar,
            text="🧹 Clear Finished",
            width=112,
            height=28,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=11),
            command=self.clear_finished_tasks,
        )
        clear_done_btn.pack(side="right", padx=3)

        open_dl_btn = ctk.CTkButton(
            queue_toolbar,
            text="📂 Output Folder",
            width=125,
            height=28,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=11),
            command=lambda: self._open_directory(self.path_entry.get().strip()),
        )
        open_dl_btn.pack(side="right", padx=3)

        resume_all_btn = ctk.CTkButton(
            queue_toolbar,
            text="▶ Resume All",
            width=98,
            height=28,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=11),
            command=self.resume_all_tasks,
        )
        resume_all_btn.pack(side="right", padx=3)

        pause_all_btn = ctk.CTkButton(
            queue_toolbar,
            text="⏸ Pause All",
            width=92,
            height=28,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=11),
            command=self.pause_all_tasks,
        )
        pause_all_btn.pack(side="right", padx=3)

        self.concurrent_menu = ctk.CTkOptionMenu(
            queue_toolbar,
            values=["1x", "2x", "3x", "4x"],
            width=64,
            height=28,
            font=ctk.CTkFont(size=11),
            fg_color=BTN_SECONDARY,
            button_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            command=self._on_concurrent_change,
        )
        self.concurrent_menu.set("2x")
        self.concurrent_menu.pack(side="right", padx=4)

        ctk.CTkLabel(
            queue_toolbar,
            text="Parallel:",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="right", padx=(8, 2))

        self.queue_scroll = ctk.CTkScrollableFrame(
            tab_queue,
            fg_color=CARD_INNER,
            corner_radius=8,
        )
        self.queue_scroll.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        self.queue_scroll.grid_columnconfigure(0, weight=1)

        self.empty_queue_lbl = ctk.CTkLabel(
            self.queue_scroll,
            text="Queue is empty.\nPaste any video or playlist link above and click '⬇ Add & Download'.",
            font=ctk.CTkFont(size=13),
            text_color=TEXT_MUTED,
            pady=60,
        )
        self.empty_queue_lbl.grid(row=0, column=0, sticky="ew")

        # Console Log Tab Layout
        tab_logs.grid_columnconfigure(0, weight=1)
        tab_logs.grid_rowconfigure(1, weight=1)

        log_toolbar = ctk.CTkFrame(tab_logs, fg_color="transparent")
        log_toolbar.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 4))

        ctk.CTkLabel(
            log_toolbar,
            text="Real-time extractor & FFmpeg post-processing output",
            font=ctk.CTkFont(size=11),
            text_color=TEXT_MUTED,
        ).pack(side="left", padx=4)

        ctk.CTkButton(
            log_toolbar,
            text="🗑 Clear Log",
            width=90,
            height=26,
            fg_color=BTN_SECONDARY,
            hover_color=BTN_SECONDARY_HOVER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(size=11),
            command=lambda: self.log_box.delete("1.0", "end"),
        ).pack(side="right", padx=4)

        self.log_box = ctk.CTkTextbox(
            tab_logs,
            fg_color=CARD_INNER,
            text_color=TEXT_MAIN,
            font=ctk.CTkFont(family="Consolas", size=12),
            corner_radius=8,
        )
        self.log_box.grid(row=1, column=0, sticky="nsew", padx=4, pady=2)
        self.log("yt-dlp Studio GUI initialized.")

    # ─────────────────────────────────────────────────────────
    #  Utility Methods
    # ─────────────────────────────────────────────────────────

    def _apply_window_icon(self) -> None:
        try:
            if sys.platform == "win32" and ICON_ICO_PATH.exists():
                self.iconbitmap(default=str(ICON_ICO_PATH))
                self.wm_iconbitmap(str(ICON_ICO_PATH))
            elif ICON_PNG_PATH.exists():
                from tkinter import PhotoImage
                self._tk_icon = PhotoImage(file=str(ICON_PNG_PATH))
                self.wm_iconphoto(True, self._tk_icon)
        except Exception:
            pass

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        clean_msg = strip_ansi(message)

        def _append() -> None:
            self.log_box.insert("end", f"[{timestamp}] {clean_msg}\n")
            self.log_box.see("end")

        self.after(0, _append)

    def _on_theme_change(self, mode: str) -> None:
        ctk.set_appearance_mode(mode)
        self._save_prefs()

    def _on_concurrent_change(self, val: str) -> None:
        try:
            self.max_concurrent = int(val.replace("x", ""))
        except ValueError:
            self.max_concurrent = 2
        self._save_prefs()
        self._schedule_workers()

    def _check_dependencies(self) -> None:
        ffmpeg_path = shutil.which("ffmpeg")
        if ffmpeg_path:
            self.ffmpeg_badge.configure(text="✔ FFmpeg Ready", text_color=SUCCESS_COLOR)
            self.log(f"FFmpeg detected at: {ffmpeg_path}")
        else:
            self.ffmpeg_badge.configure(text="⚠ FFmpeg Not Found", text_color=WARNING_COLOR)
            self.log(
                "WARNING: ffmpeg not found on PATH. Merging separate 1080p+/4K streams "
                "or MP3 conversion will fallback to single-file formats."
            )

    def _clear_url(self) -> None:
        self.url_entry.delete(0, "end")
        self.url_entry.focus_set()

    def _paste_clipboard(self) -> None:
        try:
            clip = self.clipboard_get().strip()
            if clip:
                self.url_entry.delete(0, "end")
                self.url_entry.insert(0, clip)
                if clip.startswith(("http://", "https://")):
                    self.fetch_metadata()
        except Exception:
            pass

    def _browse_folder(self) -> None:
        folder = filedialog.askdirectory(
            initialdir=self.path_entry.get().strip() or self.default_download_dir
        )
        if folder:
            self.path_entry.delete(0, "end")
            self.path_entry.insert(0, folder)
            self._save_prefs()

    def _on_preset_changed(self, selected_label: str) -> None:
        preset = next((p for p in self.available_presets if p.label == selected_label), None)
        if preset and preset.is_audio_only:
            self.container_menu.configure(state="disabled")
        else:
            self.container_menu.configure(state="normal")

    def _open_directory(self, path_str: str) -> None:
        target = Path(path_str)
        if target.is_file():
            target = target.parent
        if not target.exists():
            target = Path(self.default_download_dir)
        try:
            open_path_in_os(target)
        except Exception as e:
            self.log(f"Failed to open folder: {e}")

    def _play_file(self, task: QueueTask) -> None:
        # 1. Completed file playback
        if task.status == "Completed" and task.output_file:
            target = Path(task.output_file)
            if target.exists():
                try:
                    open_path_in_os(target)
                    return
                except Exception as e:
                    self.log(f"Failed to launch media player: {e}")
                    return

        # 2. Partial (.part) file playback while Downloading / Paused / Cancelled
        candidates: list[Path] = []
        if task.part_file:
            p = Path(task.part_file)
            if p.exists():
                candidates.append(p)
            # Also check if yt-dlp created fragment-less base .part
            if p.name.endswith("-Frag" + p.name.split("-Frag")[-1]):
                base_part = p.parent / p.name.split("-Frag")[0]
                if base_part.exists():
                    candidates.insert(0, base_part)

        if task.output_file:
            out_p = Path(task.output_file)
            if out_p.exists():
                candidates.append(out_p)
            part_p = Path(f"{task.output_file}.part")
            if part_p.exists():
                candidates.append(part_p)

        # Fallback: scan task.output_dir for most recently modified .part file
        if not candidates and Path(task.output_dir).exists():
            part_files = sorted(
                Path(task.output_dir).glob("*.part"),
                key=lambda f: f.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(part_files[:1])

        player = find_media_player()

        if candidates:
            target_part = candidates[0]
            self.log(f"Previewing partial download: {target_part.name}")
            try:
                if player:
                    subprocess.Popen([player, str(target_part)])
                else:
                    open_path_in_os(target_part)
                return
            except Exception as e:
                self.log(f"Failed to preview partial file: {e}")

        # 3. If no local .part file is playable yet, stream URL directly in external player
        if player and task.url:
            self.log(f"Streaming live URL in {Path(player).name}: {task.url}")
            try:
                subprocess.Popen([player, task.url])
                return
            except Exception as e:
                self.log(f"Failed to stream URL in player: {e}")

        messagebox.showinfo(
            "Preview Not Ready",
            "Partial media file is not ready on disk yet, or no compatible player (VLC / MPV / MPC-HC / PotPlayer) was found.",
        )

    # ─────────────────────────────────────────────────────────
    #  Metadata Analyzer (yt-dlp extract_info)
    # ─────────────────────────────────────────────────────────

    def fetch_metadata(self) -> None:
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning(
                "Input Required",
                "Please enter or paste a valid video/playlist URL first.",
            )
            return

        self._analyze_req_id += 1
        req_id = self._analyze_req_id

        self.analyze_btn.configure(state="disabled", text="⏳ Analyzing...")
        self.info_title.configure(text="Fetching media metadata...")
        self.info_uploader.configure(text="Please wait...")
        self.info_meta.configure(text="")
        self.info_badge.configure(text="Querying yt-dlp extractor...", text_color=WARNING_COLOR)

        cookies_browser = self.cookies_menu.get()

        threading.Thread(
            target=self._fetch_metadata_worker,
            args=(req_id, url, cookies_browser),
            daemon=True,
        ).start()

    def _fetch_metadata_worker(self, req_id: int, url: str, cookies_browser: str) -> None:
        self.log(f"Analyzing URL: {url}")
        ydl_opts: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        if cookies_browser and cookies_browser != "None":
            ydl_opts["cookiesfrombrowser"] = (cookies_browser,)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            if not info:
                raise RuntimeError("No metadata returned for this URL.")

            # Fetch thumbnail image if available
            thumb_url = info.get("thumbnail")
            if not thumb_url and info.get("thumbnails"):
                thumb_url = info["thumbnails"][-1].get("url")
            if not thumb_url and info.get("entries"):
                first_entry = next((e for e in info["entries"] if e), None)
                if first_entry:
                    thumb_url = first_entry.get("thumbnail") or (
                        first_entry.get("thumbnails", [{}])[-1].get("url")
                        if first_entry.get("thumbnails")
                        else None
                    )

            pil_img: Optional[Image.Image] = None
            if thumb_url:
                try:
                    resp = self._http_session.get(thumb_url, timeout=8)
                    if resp.ok:
                        pil_img = Image.open(io.BytesIO(resp.content)).convert("RGB")
                except Exception as img_err:
                    self.log(f"Thumbnail fetch skipped: {img_err}")

            self.after(0, lambda: self._update_metadata_ui(req_id, info, pil_img))

        except Exception as e:
            err_msg = strip_ansi(str(e))
            self.log(f"Analyze Error: {err_msg}")
            self.after(0, lambda: self._on_metadata_error(req_id, err_msg))

    def _update_metadata_ui(
        self,
        req_id: int,
        info: dict[str, Any],
        pil_img: Optional[Image.Image],
    ) -> None:
        if req_id != self._analyze_req_id:
            return
        self.analyze_btn.configure(state="normal", text="🔍 Analyze URL")
        self.current_info = info

        is_playlist = info.get("_type") in ("playlist", "multi_video") or "entries" in info
        title = info.get("title") or "Untitled Media"
        uploader = (
            info.get("uploader")
            or info.get("channel")
            or info.get("extractor_key")
            or "Unknown"
        )

        if is_playlist:
            entries = [e for e in (info.get("entries") or []) if e]
            count = info.get("playlist_count") or len(entries)
            self.info_title.configure(text=f"📑 [Playlist] {title}")
            self.info_uploader.configure(text=f"Creator: {uploader}")
            self.info_meta.configure(text=f"Total Items: {count} videos")
            self.info_badge.configure(
                text=f"✔ Playlist detected ({count} items). Filter range via 'Items' (e.g. 1-5).",
                text_color=SUCCESS_COLOR,
            )
            self.log(f"Playlist analyzed: '{title}' ({count} items)")
        else:
            duration_str = format_duration(info.get("duration"))
            views_str = format_count(info.get("view_count"))
            extractor = info.get("extractor_key") or "Video"
            self.info_title.configure(text=title)
            self.info_uploader.configure(text=f"Channel: {uploader}")
            self.info_meta.configure(text=f"Duration: {duration_str}   •   Views: {views_str}")

            # Build dynamic resolution presets from actual video formats
            formats = info.get("formats") or []
            heights = sorted(
                {
                    int(f["height"])
                    for f in formats
                    if f.get("height") and f.get("vcodec") != "none"
                },
                reverse=True,
            )

            custom_presets: list[FormatPreset] = [
                FormatPreset("🌟 Best Video + Audio (Auto)", "bestvideo+bestaudio/best")
            ]
            for h in heights:
                tag = (
                    "8K" if h >= 4320
                    else "4K" if h >= 2160
                    else "2K" if h >= 1440
                    else "FHD" if h >= 1080
                    else "HD" if h >= 720
                    else "SD"
                )
                custom_presets.append(
                    FormatPreset(
                        f"🎬 {h}p ({tag})",
                        f"bestvideo[height<={h}]+bestaudio/best[height<={h}]",
                    )
                )

            custom_presets.extend([p for p in self._default_presets() if p.is_audio_only])

            if heights:
                self.available_presets = custom_presets
                labels = [p.label for p in self.available_presets]
                self.format_menu.configure(values=labels)
                self.format_menu.set(labels[0])
                self._on_preset_changed(labels[0])

            res_summary = ", ".join(f"{h}p" for h in heights[:6]) if heights else "Standard"
            self.info_badge.configure(
                text=f"✔ [{extractor}] Available: {res_summary}",
                text_color=SUCCESS_COLOR,
            )
            self.log(f"Analyzed: '{title}' | Resolutions: {res_summary}")

        # Update Thumbnail
        if pil_img:
            self.current_thumb_ctk_large = ctk.CTkImage(
                light_image=pil_img, dark_image=pil_img, size=(210, 118)
            )
            self.current_thumb_ctk_small = ctk.CTkImage(
                light_image=pil_img, dark_image=pil_img, size=(92, 52)
            )
            self.thumb_box.configure(image=self.current_thumb_ctk_large, text="")
        else:
            self.current_thumb_ctk_large = None
            self.current_thumb_ctk_small = None
            self.thumb_box.configure(image=None, text="🎬\nPreview Ready")

    def _on_metadata_error(self, req_id: int, err_msg: str) -> None:
        if req_id != self._analyze_req_id:
            return
        self.analyze_btn.configure(state="normal", text="🔍 Analyze URL")
        self.info_title.configure(text="Could not analyze metadata")
        self.info_uploader.configure(text="Check the URL or select Browser Cookies if login is required")
        self.info_meta.configure(text="")
        self.info_badge.configure(text=f"✖ {err_msg[:80]}", text_color=DANGER_COLOR)

    # ─────────────────────────────────────────────────────────
    #  Queue & Download Engine
    # ─────────────────────────────────────────────────────────

    def enqueue_current(self) -> None:
        url = self.url_entry.get().strip()
        if not url:
            messagebox.showwarning("Missing URL", "Please enter a video or playlist URL first.")
            return

        output_dir = self.path_entry.get().strip() or self.default_download_dir
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        self._save_prefs()

        selected_label = self.format_menu.get()
        preset = next(
            (p for p in self.available_presets if p.label == selected_label),
            self.available_presets[0],
        )

        # Only use analyzed title/thumbnail if it matches the URL currently in the entry box
        matched_info = (
            self.current_info
            and url in (
                self.current_info.get("webpage_url"),
                self.current_info.get("original_url"),
                self.current_info.get("url"),
            )
        )
        title = (self.current_info.get("title") or url) if matched_info and self.current_info else url
        thumb = self.current_thumb_ctk_small if matched_info else None

        task_id = f"task_{int(time.time() * 1000)}_{len(self.tasks)}"
        task = QueueTask(
            task_id=task_id,
            url=url,
            title=title,
            thumbnail_img=thumb,
            preset=preset,
            container=self.container_menu.get(),
            output_dir=output_dir,
            embed_subs=self.sub_var.get(),
            embed_thumb=self.thumb_var.get(),
            embed_metadata=self.meta_var.get(),
            sponsorblock=self.sb_var.get(),
            playlist_items=self.playlist_entry.get().strip(),
            cookies_browser=self.cookies_menu.get(),
        )

        self._add_task_card(task)
        self._update_queue_header()
        self._save_prefs()

        self.log(f"Queued: {task.title} ({preset.label})")
        self._schedule_workers()

    def _add_task_card(self, task: QueueTask) -> None:
        self.empty_queue_lbl.grid_forget()

        card = QueueItemCard(
            self.queue_scroll,
            task=task,
            on_move_up=lambda t: self.move_task(t, -1),
            on_move_down=lambda t: self.move_task(t, 1),
            on_pause=self.pause_task,
            on_resume=self.resume_task,
            on_cancel=self.cancel_task,
            on_remove=self.remove_task,
            on_open_folder=lambda t: self._open_directory(t.output_file or t.output_dir),
            on_play_file=self._play_file,
        )
        card.grid(row=len(self.tasks), column=0, sticky="ew", padx=6, pady=5)
        card.refresh_ui()

        self.tasks.append(task)
        self.task_cards[task.task_id] = card
        self._relayout_queue_cards()

    def _relayout_queue_cards(self) -> None:
        total = len(self.tasks)
        for idx, t in enumerate(self.tasks):
            card = self.task_cards.get(t.task_id)
            if card:
                card.grid(row=idx, column=0, sticky="ew", padx=6, pady=5)
                card.set_order_state(idx, total)

    def move_task(self, task: QueueTask, direction: int) -> None:
        with self.queue_lock:
            if task not in self.tasks:
                return
            idx = self.tasks.index(task)
            new_idx = idx + direction
            if new_idx < 0 or new_idx >= len(self.tasks):
                return
            self.tasks[idx], self.tasks[new_idx] = self.tasks[new_idx], self.tasks[idx]
        self._relayout_queue_cards()
        self._save_prefs()

    def _update_queue_header(self) -> None:
        total = len(self.tasks)
        active = sum(1 for t in self.tasks if t.status in ("Downloading", "Processing"))
        paused = sum(1 for t in self.tasks if t.status == "Paused")
        done = sum(1 for t in self.tasks if t.status == "Completed")
        queued = sum(1 for t in self.tasks if t.status == "Queued")

        self.queue_summary_lbl.configure(
            text=(
                f"Total: {total}   •   Active: {active}   •   "
                f"Paused: {paused}   •   Queued: {queued}   •   Completed: {done}"
            )
        )
        if total == 0:
            self.empty_queue_lbl.grid(row=0, column=0, sticky="ew")

    def pause_task(self, task: QueueTask) -> None:
        if task.status == "Queued":
            task.status = "Paused"
            self._refresh_task_card(task)
            self._update_queue_header()
            self._save_prefs()
            self.log(f"Paused queued task: {task.title}")
        elif task.status == "Downloading":
            task.stop_reason = "pause"
            task.cancel_event.set()
            self.log(f"Pausing download: {task.title}...")

    def resume_task(self, task: QueueTask) -> None:
        if task.status not in ("Paused", "Cancelled", "Error"):
            return
        task.cancel_event.clear()
        task.stop_reason = ""
        task.status = "Queued"
        task.speed_str = "—"
        task.eta_str = "—"
        task.error_msg = ""
        self._refresh_task_card(task)
        self._update_queue_header()
        self._save_prefs()
        self.log(f"Resuming download: {task.title}")
        self._schedule_workers()

    def pause_all_tasks(self) -> None:
        for t in self.tasks:
            if t.status in ("Queued", "Downloading"):
                self.pause_task(t)

    def resume_all_tasks(self) -> None:
        for t in self.tasks:
            if t.status in ("Paused", "Cancelled", "Error"):
                self.resume_task(t)

    def cancel_task(self, task: QueueTask) -> None:
        if task.status == "Queued":
            task.status = "Cancelled"
            self._refresh_task_card(task)
            self._update_queue_header()
            self._save_prefs()
        elif task.status in ("Downloading", "Processing"):
            task.stop_reason = "cancel"
            task.cancel_event.set()
            self.log(f"Stopping task: {task.title}...")

    def remove_task(self, task: QueueTask) -> None:
        card = self.task_cards.pop(task.task_id, None)
        if card:
            card.destroy()
        if task in self.tasks:
            self.tasks.remove(task)
        self._relayout_queue_cards()
        self._update_queue_header()
        self._save_prefs()

    def clear_finished_tasks(self) -> None:
        finished = [t for t in self.tasks if t.status in ("Completed", "Cancelled", "Error")]
        for t in finished:
            self.remove_task(t)

    def _refresh_task_card(self, task: QueueTask) -> None:
        card = self.task_cards.get(task.task_id)
        if card:
            card.refresh_ui()

    def _schedule_workers(self) -> None:
        with self.queue_lock:
            while self.active_workers < self.max_concurrent:
                next_task = next((t for t in self.tasks if t.status == "Queued"), None)
                if not next_task:
                    break
                next_task.status = "Downloading"
                self.active_workers += 1
                self.after(0, lambda t=next_task: self._refresh_task_card(t))
                self.after(0, self._update_queue_header)
                threading.Thread(
                    target=self._run_download_worker,
                    args=(next_task,),
                    daemon=True,
                ).start()

    def _run_download_worker(self, task: QueueTask) -> None:
        has_ffmpeg = shutil.which("ffmpeg") is not None

        def progress_hook(d: dict[str, Any]) -> None:
            if task.cancel_event.is_set():
                raise DownloadCancelledError("Download cancelled by user.")

            status = d.get("status")
            info_dict = d.get("info_dict") or {}

            if info_dict.get("title") and task.title == task.url:
                task.title = info_dict["title"]

            pl_idx = info_dict.get("playlist_index")
            pl_count = info_dict.get("playlist_count") or info_dict.get("n_entries")
            if pl_idx and pl_count:
                task.playlist_progress = f"{pl_idx}/{pl_count}"

            if status == "downloading":
                task.status = "Downloading"
                tmp_fn = d.get("tmpfilename") or d.get("filename")
                if tmp_fn and tmp_fn != "-":
                    task.part_file = str(tmp_fn)
                if d.get("filename"):
                    task.output_file = str(d["filename"])

                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                downloaded = d.get("downloaded_bytes") or 0

                if total > 0:
                    task.progress = downloaded / total
                    task.size_str = f"{format_bytes(downloaded)} / {format_bytes(total)}"
                else:
                    pct_raw = strip_ansi(d.get("_percent_str", "0%")).replace("%", "").strip()
                    try:
                        task.progress = float(pct_raw) / 100.0
                    except ValueError:
                        pass
                    task.size_str = format_bytes(downloaded)

                spd = d.get("speed")
                task.speed_str = (
                    f"{format_bytes(spd)}/s"
                    if spd
                    else strip_ansi(d.get("_speed_str", "—")).strip()
                )

                eta = d.get("eta")
                task.eta_str = (
                    format_duration(eta)
                    if eta is not None
                    else strip_ansi(d.get("_eta_str", "—")).strip()
                )

                # Throttle UI updates to ~12 fps per worker to keep Tkinter responsive
                now = time.monotonic()
                if now - task.last_ui_ts >= 0.08:
                    task.last_ui_ts = now
                    self.after(0, lambda: self._refresh_task_card(task))

            elif status == "finished":
                filename = d.get("filename")
                if filename:
                    task.output_file = filename
                total = d.get("total_bytes") or d.get("downloaded_bytes") or 0
                if total:
                    task.size_str = format_bytes(total)
                task.progress = 1.0
                task.status = "Processing"
                self.after(0, lambda: self._refresh_task_card(task))

        def postprocessor_hook(d: dict[str, Any]) -> None:
            if task.cancel_event.is_set():
                raise DownloadCancelledError("Download cancelled by user.")
            if d.get("status") == "started":
                task.status = "Processing"
                self.after(0, lambda: self._refresh_task_card(task))
            elif d.get("status") == "finished":
                filepath = (d.get("info_dict") or {}).get("filepath")
                if filepath:
                    task.output_file = filepath

        outtmpl = str(Path(task.output_dir) / "%(title).180B [%(id)s].%(ext)s")

        fmt_spec = task.preset.format_spec
        if not has_ffmpeg and "+" in fmt_spec:
            fmt_spec = "best"

        ydl_opts: dict[str, Any] = {
            "format": fmt_spec,
            "outtmpl": {"default": outtmpl},
            "continuedl": True,
            "part": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
            "logger": _YtDlpLogger(self.log),
            "noplaylist": not bool(task.playlist_items),
            "concurrent_fragment_downloads": 4,
            "retries": 5,
            "fragment_retries": 5,
            "ignoreerrors": False,
            "quiet": True,
            "no_warnings": True,
        }

        if task.playlist_items:
            ydl_opts["noplaylist"] = False
            ydl_opts["playlist_items"] = task.playlist_items
        elif "list=" in task.url or "/playlist" in task.url:
            ydl_opts["noplaylist"] = False

        if task.cookies_browser and task.cookies_browser != "None":
            ydl_opts["cookiesfrombrowser"] = (task.cookies_browser,)

        postprocessors: list[dict[str, Any]] = []

        if task.preset.is_audio_only:
            if has_ffmpeg:
                postprocessors.append(
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": task.preset.audio_codec,
                        "preferredquality": task.preset.audio_quality,
                    }
                )
        else:
            if has_ffmpeg:
                ydl_opts["merge_output_format"] = task.container

        if task.sponsorblock and has_ffmpeg:
            postprocessors.append(
                {
                    "key": "SponsorBlock",
                    "categories": ["sponsor", "selfpromo", "interaction"],
                }
            )
            postprocessors.append(
                {
                    "key": "ModifyChapters",
                    "remove_sponsor_segments": ["sponsor", "selfpromo", "interaction"],
                }
            )

        if task.embed_subs:
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
            ydl_opts["subtitleslangs"] = ["en.*", "id.*", "all"]
            if has_ffmpeg:
                postprocessors.append({"key": "FFmpegEmbedSubtitle"})

        if task.embed_thumb and has_ffmpeg:
            ydl_opts["writethumbnail"] = True
            postprocessors.append({"key": "EmbedThumbnail"})

        if task.embed_metadata and has_ffmpeg:
            postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True})

        if postprocessors:
            ydl_opts["postprocessors"] = postprocessors

        try:
            self.log(f"Starting download: {task.url} | Format: {fmt_spec}")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(task.url, download=True)
                if info:
                    if info.get("title"):
                        task.title = info["title"]
                    if not task.output_file:
                        prepared = ydl.prepare_filename(info)
                        if task.preset.is_audio_only and has_ffmpeg:
                            prepared = str(Path(prepared).with_suffix(f".{task.preset.audio_codec}"))
                        elif has_ffmpeg and not task.preset.is_audio_only:
                            prepared = str(Path(prepared).with_suffix(f".{task.container}"))
                        task.output_file = prepared

            if task.output_file and Path(task.output_file).exists():
                task.size_str = format_bytes(Path(task.output_file).stat().st_size)

            task.status = "Completed"
            task.progress = 1.0
            self.log(f"Completed: {task.title}")

        except DownloadCancelledError:
            if task.stop_reason == "pause":
                task.status = "Paused"
                self.log(f"Paused: {task.title} ({task.progress * 100:.1f}%)")
            else:
                task.status = "Cancelled"
                self.log(f"Stopped: {task.title}")

        except Exception as e:
            if task.cancel_event.is_set():
                if task.stop_reason == "pause":
                    task.status = "Paused"
                    self.log(f"Paused: {task.title} ({task.progress * 100:.1f}%)")
                else:
                    task.status = "Cancelled"
                    self.log(f"Stopped: {task.title}")
            else:
                task.status = "Error"
                task.error_msg = strip_ansi(str(e))
                self.log(f"Error downloading '{task.title}': {task.error_msg}")

        finally:
            self.after(0, lambda: self._refresh_task_card(task))
            self.after(0, self._update_queue_header)
            self.after(0, self._save_prefs)
            with self.queue_lock:
                self.active_workers -= 1
            self._schedule_workers()


def main() -> None:
    app = YtDlpGuiApp()
    app.mainloop()


if __name__ == "__main__":
    main()
