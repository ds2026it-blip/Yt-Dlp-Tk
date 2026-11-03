#!/usr/bin/env python3
"""
stream_dl.py — HLS / M3U8 Stream Downloader
Accepts either a direct .m3u8 URL *or* any web-page URL (including
JavaScript-rendered pages). Two discovery strategies are used:

  1. Static scan  — fast, works when the URL is in raw HTML / JS source
  2. Browser scan — headless Chromium intercepts real network requests;
                    works on any JS-rendered SPA / streaming site

Usage:
    python stream_dl.py <URL> [OPTIONS]

Examples:
    # Auto-detect (static first, browser fallback)
    python stream_dl.py "https://example.com/watch/episode-1"

    # Force browser mode immediately (recommended for JS-heavy sites)
    python stream_dl.py "https://example.com/watch/ep1" --browser

    # Wait longer for slow-loading players
    python stream_dl.py "https://example.com/watch/ep1" --browser --wait 20

    # Direct .m3u8 URL — skips discovery entirely
    python stream_dl.py "https://cdn.example.com/hls/index.m3u8" -o movie.mp4

    # With auth headers
    python stream_dl.py "https://example.com/watch/ep1" \\
        --headers "Cookie:session=abc123"
"""

import argparse
import re
import subprocess
import sys
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup


# ─────────────────────────────────────────────────────────────
#  ANSI colours
# ─────────────────────────────────────────────────────────────

RESET   = "\033[0m"
BOLD    = "\033[1m"
GREEN   = "\033[92m"
YELLOW  = "\033[93m"
RED     = "\033[91m"
CYAN    = "\033[96m"
BLUE    = "\033[94m"
MAGENTA = "\033[95m"
DIM     = "\033[2m"


def cprint(msg: str, color: str = RESET, bold: bool = False) -> None:
    prefix = BOLD if bold else ""
    print(f"{prefix}{color}{msg}{RESET}", flush=True)


def banner() -> None:
    print()
    cprint("╔════════════════════════════════════════════════════╗", CYAN, bold=True)
    cprint("║      stream_dl  •  HLS / M3U8 Stream Downloader   ║", CYAN, bold=True)
    cprint("║  Browser intercept · Quality picker · Zero encode  ║", CYAN, bold=True)
    cprint("╚════════════════════════════════════════════════════╝", CYAN, bold=True)
    print()


# ─────────────────────────────────────────────────────────────
#  Spinner
# ─────────────────────────────────────────────────────────────

class Spinner:
    """A simple terminal spinner that runs in a background thread."""
    _FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, message: str = ""):
        self._msg     = message
        self._stop    = threading.Event()
        self._thread  = threading.Thread(target=self._spin, daemon=True)

    def _spin(self) -> None:
        i = 0
        while not self._stop.is_set():
            frame = self._FRAMES[i % len(self._FRAMES)]
            print(f"\r  {CYAN}{frame}{RESET}  {self._msg}", end="", flush=True)
            time.sleep(0.08)
            i += 1

    def update(self, msg: str) -> None:
        self._msg = msg

    def start(self) -> "Spinner":
        self._thread.start()
        return self

    def stop(self, final_msg: str = "", ok: bool = True) -> None:
        self._stop.set()
        self._thread.join()
        icon = f"{GREEN}✔{RESET}" if ok else f"{RED}✘{RESET}"
        suffix = f"  {final_msg}" if final_msg else ""
        print(f"\r  {icon}{suffix}              ", flush=True)


# ─────────────────────────────────────────────────────────────
#  Data types
# ─────────────────────────────────────────────────────────────

@dataclass
class StreamVariant:
    url:        str
    label:      str = ""
    bandwidth:  int = 0
    resolution: str = ""
    source:     str = "page"   # "direct" | "static" | "browser" | "master"


# ─────────────────────────────────────────────────────────────
#  Static discovery (requests + BeautifulSoup + regex)
# ─────────────────────────────────────────────────────────────

_M3U8_RE_LIST: list[re.Pattern] = [
    re.compile(r"""['"`]([^'"`\s]{5,}\.m3u8(?:\?[^'"`\s]*)?)['"`]""",      re.I),
    re.compile(r"""(?:src|file|source|url|hls|stream|playlist)\s*[=:]\s*['"`]([^'"`\s]+\.m3u8[^'"`\s]*)['"`]""", re.I),
    re.compile(r"""(https?://[^\s<>"'\\]+?\.m3u8(?:\?[^\s<>"'\\]*)?)""",   re.I),
]

_RESOLUTION_RE = re.compile(r'RESOLUTION=(\d+x\d+)', re.I)
_BANDWIDTH_RE  = re.compile(r'BANDWIDTH=(\d+)',       re.I)
_NAME_RE       = re.compile(r'NAME="([^"]+)"',        re.I)


def _abs(url: str, base: str) -> str:
    return urllib.parse.urljoin(base, url)


def _dedupe(variants: list[StreamVariant]) -> list[StreamVariant]:
    seen: set[str] = set()
    out: list[StreamVariant] = []
    for v in variants:
        if v.url not in seen:
            seen.add(v.url)
            out.append(v)
    return out


def _fetch(url: str, session: requests.Session, timeout: int) -> Optional[requests.Response]:
    try:
        r = session.get(url, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return r
    except requests.RequestException:
        return None


def _parse_master(m3u8_text: str, master_url: str) -> list[StreamVariant]:
    variants: list[StreamVariant] = []
    lines = m3u8_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.upper().startswith("#EXT-X-STREAM-INF"):
            bw  = int(m.group(1)) if (m := _BANDWIDTH_RE.search(line)) else 0
            res = m.group(1)      if (m := _RESOLUTION_RE.search(line)) else ""
            nm  = m.group(1)      if (m := _NAME_RE.search(line))       else ""
            j = i + 1
            while j < len(lines) and lines[j].strip().startswith("#"):
                j += 1
            if j < len(lines):
                vurl  = _abs(lines[j].strip(), master_url)
                label = nm or res or (f"{bw//1000}k" if bw else vurl.split("/")[-1])
                variants.append(StreamVariant(url=vurl, label=label,
                                              bandwidth=bw, resolution=res,
                                              source="master"))
                i = j + 1
                continue
        i += 1
    return variants


def _probe_and_expand(url: str, session: requests.Session, timeout: int,
                      source_tag: str) -> list[StreamVariant]:
    """Fetch url; if it's a master playlist return its variants, else return [url]."""
    resp = _fetch(url, session, timeout)
    text = (resp.text.lstrip() if resp else "")
    if text.startswith("#EXTM3U"):
        variants = _parse_master(text, url)
        if variants:
            return variants
    label = url.split("/")[-1].split("?")[0] or "stream"
    return [StreamVariant(url=url, label=label, source=source_tag)]


def _extract_urls_from_text(text: str, base: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for pat in _M3U8_RE_LIST:
        for m in pat.finditer(text):
            u = _abs(m.group(1).strip(), base)
            if u not in seen:
                seen.add(u)
                found.append(u)
    return found


def _extract_urls_from_html(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    attrs = ("src", "data-src", "data-hls", "data-url", "data-stream",
             "data-video-url", "data-m3u8", "data-source", "href", "content")
    found: list[str] = []
    seen:  set[str]  = set()
    for tag in soup.find_all(True):
        for attr in attrs:
            val = tag.get(attr, "")
            if isinstance(val, list):
                val = " ".join(val)
            if ".m3u8" in val.lower():
                u = _abs(val.strip(), base)
                if u not in seen:
                    seen.add(u)
                    found.append(u)
    return found


def static_discover(
    page_url: str,
    session: requests.Session,
    timeout: int,
    quiet: bool,
) -> list[StreamVariant]:
    """Fast static discovery via requests + regex + BeautifulSoup."""
    resp = _fetch(page_url, session, timeout)
    if resp is None:
        return []

    content_type = resp.headers.get("content-type", "").lower()
    raw = resp.text

    # ── Direct .m3u8 ────────────────────────────────────────
    is_m3u8 = (
        ".m3u8" in page_url.lower().split("?")[0]
        or "mpegurl" in content_type
        or raw.lstrip().startswith("#EXTM3U")
    )
    if is_m3u8:
        variants = _parse_master(raw, page_url)
        if variants:
            return variants
        return [StreamVariant(url=page_url, label="stream", source="direct")]

    # ── Web page ─────────────────────────────────────────────
    raw_urls: list[str] = []
    for u in _extract_urls_from_html(raw, page_url):
        if u not in raw_urls:
            raw_urls.append(u)
    for u in _extract_urls_from_text(raw, page_url):
        if u not in raw_urls:
            raw_urls.append(u)

    if not raw_urls:
        return []

    all_variants: list[StreamVariant] = []
    for u in raw_urls:
        all_variants.extend(_probe_and_expand(u, session, timeout, "static"))

    return _dedupe(all_variants)


# ─────────────────────────────────────────────────────────────
#  Browser discovery (Playwright — headless Chromium)
# ─────────────────────────────────────────────────────────────

def _check_playwright() -> bool:
    """Return True if playwright + chromium are available."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def browser_discover(
    page_url: str,
    extra_headers: dict[str, str],
    user_agent: str,
    wait_seconds: int,
    timeout: int,
    session: requests.Session,
    quiet: bool,
    headless: bool = True,
) -> list[StreamVariant]:
    """
    Launch headless Chromium, navigate to the page, intercept every network
    request and collect .m3u8 URLs as they are fired by the JS player.
    """
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        cprint(
            "\n[ERROR] Playwright is not installed.\n"
            "  Run:  pip install playwright && playwright install chromium",
            RED, bold=True,
        )
        sys.exit(1)

    intercepted: list[str] = []   # ordered, deduped
    seen_urls:   set[str]  = set()
    lock = threading.Lock()

    spinner = None
    if not quiet:
        spinner = Spinner(f"Launching browser  (waiting up to {wait_seconds}s for player to load)…")
        spinner.start()

    def on_request(request) -> None:
        url = request.url
        if ".m3u8" in url.lower():
            with lock:
                if url not in seen_urls:
                    seen_urls.add(url)
                    intercepted.append(url)
                    if spinner and not quiet:
                        spinner.update(
                            f"Intercepted {len(intercepted)} stream(s) — "
                            f"still watching… ({wait_seconds}s)"
                        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=user_agent,
            extra_http_headers=extra_headers,
            ignore_https_errors=True,
        )
        page = ctx.new_page()
        page.on("request", on_request)

        try:
            page.goto(page_url, wait_until="domcontentloaded",
                      timeout=timeout * 1000)
        except PWTimeout:
            pass  # page loaded enough for the player to initialize

        # Scroll to trigger lazy-loaded players
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
        except Exception:
            pass

        # Click the first visible play button if any
        for selector in (
            "button[class*='play']", "div[class*='play']",
            "[aria-label*='play' i]", "[title*='play' i]",
            "video", ".vjs-big-play-button", ".jwplayer",
        ):
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    el.click(timeout=2000)
                    break
            except Exception:
                pass

        # Wait for streams to appear, checking every 0.5s
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            time.sleep(0.5)
            with lock:
                count = len(intercepted)
            # Early exit: if we've seen at least one URL and nothing new
            # has arrived for 3 seconds, stop waiting
            if count > 0:
                time.sleep(3)
                with lock:
                    if len(intercepted) == count:
                        break

        browser.close()

    if spinner:
        with lock:
            n = len(intercepted)
        spinner.stop(
            f"Browser scan complete — {n} stream URL(s) intercepted",
            ok=(n > 0),
        )

    if not intercepted:
        return []

    # Probe each intercepted URL (may be master playlists)
    all_variants: list[StreamVariant] = []
    for url in intercepted:
        all_variants.extend(_probe_and_expand(url, session, timeout, "browser"))

    return _dedupe(all_variants)


# ─────────────────────────────────────────────────────────────
#  Stream picker
# ─────────────────────────────────────────────────────────────

def pick_stream(variants: list[StreamVariant], quiet: bool) -> StreamVariant:
    if len(variants) == 1:
        v = variants[0]
        if not quiet:
            cprint(f"  Auto-selected :  {v.label or v.url}", GREEN)
        return v

    # Sort best quality first
    sorted_v = sorted(variants, key=lambda x: x.bandwidth, reverse=True)

    print()
    cprint("  ┌─ Available streams ─────────────────────────────────────────┐", CYAN)
    for i, v in enumerate(sorted_v, 1):
        parts: list[str] = []
        if v.resolution:
            parts.append(v.resolution)
        if v.bandwidth:
            parts.append(f"{v.bandwidth // 1000} kbps")
        if v.label and v.label not in (v.resolution, "stream"):
            parts.append(v.label)
        meta = "  ·  ".join(parts) if parts else "unknown quality"
        src_tag = f"{DIM}[{v.source}]{RESET}"
        short_url = v.url if len(v.url) <= 72 else v.url[:69] + "…"
        print(f"  {CYAN}│{RESET}  {BOLD}{YELLOW}[{i:>2}]{RESET}  {meta}  {src_tag}")
        cprint(f"  │       {short_url}", DIM)
    cprint("  └──────────────────────────────────────────────────────────────┘", CYAN)
    print()

    while True:
        try:
            raw = input(f"  {BOLD}Pick a stream [1–{len(sorted_v)}] : {RESET}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            cprint("\n[ABORT] Cancelled.", YELLOW, bold=True)
            sys.exit(130)
        if raw.isdigit() and 1 <= int(raw) <= len(sorted_v):
            print()
            return sorted_v[int(raw) - 1]
        cprint(f"  Please enter a number between 1 and {len(sorted_v)}.", YELLOW)


# ─────────────────────────────────────────────────────────────
#  ffmpeg
# ─────────────────────────────────────────────────────────────

def check_ffmpeg() -> str:
    for candidate in ("ffmpeg", "ffmpeg.exe"):
        try:
            r = subprocess.run([candidate, "-version"], capture_output=True)
            if r.returncode == 0:
                return candidate
        except FileNotFoundError:
            continue
    cprint("\n[ERROR] ffmpeg not found in PATH.", RED, bold=True)
    cprint(
        "  • Windows : https://www.gyan.dev/ffmpeg/builds/\n"
        "  • macOS   : brew install ffmpeg\n"
        "  • Linux   : sudo apt install ffmpeg\n",
        YELLOW,
    )
    sys.exit(1)


def build_ffmpeg_cmd(
    ffmpeg: str,
    stream_url: str,
    output: Path,
    headers: dict[str, str],
    referer: str,
    user_agent: str,
    timeout: int,
    verbose: bool,
) -> list[str]:
    ext = output.suffix.lower().lstrip(".")
    cmd = [ffmpeg]
    if not verbose:
        cmd += ["-loglevel", "warning", "-stats"]
    if user_agent:
        cmd += ["-user_agent", user_agent]

    all_hdrs = {}
    if referer:
        all_hdrs["Referer"] = referer
    all_hdrs.update(headers)
    for k, v in all_hdrs.items():
        cmd += ["-headers", f"{k}: {v}\r\n"]

    cmd += [
        "-timeout",             str(timeout * 1_000_000),
        "-reconnect",           "1",
        "-reconnect_streamed",  "1",
        "-reconnect_delay_max", "5",
    ]
    cmd += ["-allowed_extensions", "ALL", "-i", stream_url, "-c", "copy"]
    if ext == "mp4":
        cmd += ["-movflags", "faststart", "-bsf:a", "aac_adtstoasc"]
    cmd += ["-y", str(output)]
    return cmd


# ─────────────────────────────────────────────────────────────
#  Filename helpers
# ─────────────────────────────────────────────────────────────

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip()


_GENERIC_NAMES = {
    "index", "master", "playlist", "stream", "chunklist",
    "index.m3u8", "master.m3u8", "playlist.m3u8", "stream.m3u8",
}


def derive_output_path(page_url: str, stream_url: str, ext: str) -> Path:
    for url in (page_url, stream_url):
        path_part = url.split("?")[0].rstrip("/")
        for seg in reversed(path_part.split("/")):
            clean = sanitize_filename(seg)
            base = re.sub(r"\.(m3u8|ts|mp4|mkv|html?|php|aspx?)$", "",
                          clean, flags=re.I)
            if base.lower() not in _GENERIC_NAMES and len(base) >= 3:
                return Path(f"{base}.{ext}")
    return Path(f"stream_output.{ext}")


# ─────────────────────────────────────────────────────────────
#  Argument parsing
# ─────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download an HLS (.m3u8) stream from a direct URL or any web page.\n"
            "Supports static HTML scraping and headless-browser interception\n"
            "for JavaScript-rendered streaming sites."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("url", help="Web-page URL or direct .m3u8 URL.")
    parser.add_argument("-o", "--output", metavar="FILE",
                        help="Output file (.mp4 or .mkv).")
    parser.add_argument("-f", "--format", choices=["mp4", "mkv"], default="mp4",
                        help="Output format when --output is omitted (default: mp4).")
    parser.add_argument("--headers", action="append", metavar="KEY:VALUE", default=[],
                        help="Extra HTTP headers (repeatable).")
    parser.add_argument("--user-agent", metavar="UA",
                        default=(
                            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/124.0.0.0 Safari/537.36"
                        ),
                        help="User-Agent string.")
    parser.add_argument("--timeout", type=int, default=30, metavar="SECONDS",
                        help="Network timeout in seconds (default: 30).")

    # Browser options
    bg = parser.add_argument_group("browser options")
    bg.add_argument("--browser", action="store_true",
                    help=(
                        "Use headless Chromium to intercept stream requests.\n"
                        "Required for JavaScript-heavy streaming sites.\n"
                        "Falls back automatically if static scan finds nothing."
                    ))
    bg.add_argument("--wait", type=int, default=10, metavar="SECONDS",
                    help="How long to watch for stream requests in browser mode (default: 10).")
    bg.add_argument("--no-headless", action="store_true",
                    help="Show the browser window (useful for debugging / login).")

    parser.add_argument("--no-referer", action="store_true",
                        help="Do not send page URL as Referer to ffmpeg.")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Show full ffmpeg output.")
    parser.add_argument("-q", "--quiet", action="store_true",
                        help="Suppress all output except errors.")
    return parser.parse_args()


# ─────────────────────────────────────────────────────────────
#  Main
# ─────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()

    if not args.quiet:
        banner()

    if not args.url.startswith(("http://", "https://", "rtmp://", "rtsp://")):
        cprint("[ERROR] URL must start with http://, https://, rtmp://, or rtsp://",
               RED, bold=True)
        sys.exit(1)

    # ── Parse extra headers ─────────────────────────────────────
    extra_headers: dict[str, str] = {}
    for h in args.headers:
        if ":" not in h:
            cprint(f"[WARN] Skipping malformed header: {h!r}", YELLOW)
            continue
        k, _, v = h.partition(":")
        extra_headers[k.strip()] = v.strip()

    # ── Shared requests session ─────────────────────────────────
    session = requests.Session()
    session.headers.update({
        "User-Agent":      args.user_agent,
        "Accept":          "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    session.headers.update(extra_headers)

    # ─────────────────────────────────────────────────────────────
    #  STEP 1 — Discovery
    # ─────────────────────────────────────────────────────────────
    if not args.quiet:
        cprint("  Step 1/3  :  Discovering streams …", YELLOW, bold=True)
        print()

    variants: list[StreamVariant] = []

    # Always do the quick static scan first (unless --browser forced)
    if not args.browser:
        if not args.quiet:
            cprint("  Method    :  Static scan (HTML + JS source)", DIM)
        spin = Spinner("Fetching page and scanning for stream URLs …")
        if not args.quiet:
            spin.start()
        try:
            variants = static_discover(args.url, session, args.timeout, quiet=True)
        except KeyboardInterrupt:
            if not args.quiet:
                spin.stop("Cancelled", ok=False)
            sys.exit(130)
        if not args.quiet:
            spin.stop(
                f"Static scan complete — {len(variants)} stream(s) found",
                ok=(len(variants) > 0),
            )

    # If static found nothing (or --browser forced), try browser interception
    if not variants:
        if not _check_playwright():
            cprint(
                "\n[ERROR] No streams found via static scan, and Playwright is not installed.\n"
                "  Install it to enable browser-based interception:\n\n"
                "    pip install playwright\n"
                "    playwright install chromium\n",
                RED, bold=True,
            )
            sys.exit(1)

        if not args.quiet:
            if not args.browser:
                print()
                cprint("  Static scan found nothing — switching to browser mode …", YELLOW)
            else:
                cprint("  Method    :  Browser interception (headless Chromium)", DIM)
            print()

        variants = browser_discover(
            page_url=args.url,
            extra_headers=extra_headers,
            user_agent=args.user_agent,
            wait_seconds=args.wait,
            timeout=args.timeout,
            session=session,
            quiet=args.quiet,
            headless=not args.no_headless,
        )

    if not variants:
        cprint(
            "\n[ERROR] No .m3u8 streams found.\n\n"
            "  Suggestions:\n"
            "  • Try --browser  to use headless Chromium interception.\n"
            "  • Try --wait 20  to wait longer for slow players.\n"
            "  • Try --no-headless to see the browser and interact with it\n"
            "    (e.g. dismiss a login popup or accept cookies).\n"
            "  • The stream may use DASH (.mpd) instead of HLS — not supported yet.\n"
            "  • If you can see the stream in your browser, open DevTools →\n"
            "    Network tab → filter 'm3u8' → copy the URL and pass it directly.",
            RED, bold=True,
        )
        sys.exit(1)

    # ─────────────────────────────────────────────────────────────
    #  STEP 2 — Pick stream
    # ─────────────────────────────────────────────────────────────
    if not args.quiet:
        print()
        cprint(f"  Step 2/3  :  Stream selection  ({len(variants)} found)", YELLOW, bold=True)

    chosen = pick_stream(variants, quiet=args.quiet)

    # ─────────────────────────────────────────────────────────────
    #  STEP 3 — Download
    # ─────────────────────────────────────────────────────────────
    if args.output:
        output = Path(args.output)
        ext = output.suffix.lower().lstrip(".")
        if ext not in ("mp4", "mkv"):
            cprint(f"[ERROR] Unsupported extension '.{ext}'. Use .mp4 or .mkv",
                   RED, bold=True)
            sys.exit(1)
    else:
        output = derive_output_path(args.url, chosen.url, args.format)

    output.parent.mkdir(parents=True, exist_ok=True)
    referer = "" if args.no_referer else args.url
    ffmpeg  = check_ffmpeg()

    if not args.quiet:
        print()
        cprint("  Step 3/3  :  Downloading …", YELLOW, bold=True)
        print()
        cprint(f"  Page      :  {args.url}", DIM)
        cprint(f"  Stream    :  {chosen.url}", DIM)
        if chosen.resolution:
            cprint(f"  Quality   :  {chosen.resolution}", DIM)
        if chosen.bandwidth:
            cprint(f"  Bitrate   :  {chosen.bandwidth // 1000} kbps", DIM)
        cprint(f"  Output    :  {output}", DIM)
        cprint(f"  Container :  {output.suffix.upper().lstrip('.')} (stream copy — no re-encode)", DIM)
        print()

    cmd = build_ffmpeg_cmd(
        ffmpeg=ffmpeg,
        stream_url=chosen.url,
        output=output,
        headers=extra_headers,
        referer=referer,
        user_agent=args.user_agent,
        timeout=args.timeout,
        verbose=args.verbose,
    )

    if args.verbose and not args.quiet:
        cprint("  ffmpeg command:", DIM)
        print("  " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
        print()

    start = time.monotonic()
    try:
        result = subprocess.run(cmd, check=False)
    except KeyboardInterrupt:
        print()
        cprint("\n[ABORT] Download cancelled by user.", YELLOW, bold=True)
        if output.exists():
            size_mb = output.stat().st_size / 1_048_576
            cprint(f"  Partial file kept : {output.resolve()}  ({size_mb:.1f} MB)", DIM)
        sys.exit(130)

    elapsed = time.monotonic() - start

    if result.returncode != 0:
        print()
        cprint(f"[ERROR] ffmpeg exited with code {result.returncode}.", RED, bold=True)
        cprint(
            "  Tips:\n"
            "  • Add -v to see full ffmpeg output.\n"
            "  • Try --no-referer if the CDN rejects the Referer header.\n"
            "  • Pass --headers 'Cookie:...' for login-gated streams.",
            YELLOW,
        )
        sys.exit(result.returncode)

    if not args.quiet:
        size_mb = output.stat().st_size / 1_048_576 if output.exists() else 0
        print()
        cprint("✔  Download complete!", GREEN, bold=True)
        cprint(f"   File    : {output.resolve()}", GREEN)
        cprint(f"   Size    : {size_mb:.1f} MB", GREEN)
        cprint(f"   Time    : {elapsed:.1f}s", GREEN)
        print()


if __name__ == "__main__":
    main()
