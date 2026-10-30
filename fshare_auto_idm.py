# -*- coding: utf-8 -*-
"""
Fshare Auto Download v6.1 by Duy Anh
- Quét các thư mục Fshare, lấy tập cập nhật trong ngày (hoặc N ngày gần đây)
- Mở link từng tập mới trên Firefox (đã đăng nhập Fshare VIP) -> IDM bắt link -> tự bấm "Bắt đầu tải"
- Theo dõi thư mục lưu của IDM, tải xong thì chuyển trạng thái "Đang tải" -> "Xong"
- Ghi nhớ các tập đã tải để lần sau bỏ qua
Chạy:  python fshare_auto_idm.py          (mở giao diện)
       python fshare_auto_idm.py --auto   (mở giao diện và tự Quét & Tải ngay)
"""
import datetime as dt
import json
import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, messagebox, filedialog

try:
    import requests
except ImportError:
    requests = None

try:  # khay hệ thống + thông báo Windows (pip install pystray pillow)
    import pystray
    from PIL import Image as PILImage
    TRAY_OK = os.name == "nt"
except Exception:
    pystray = PILImage = None
    TRAY_OK = False

APP_TITLE = "Fshare Auto Download"
APP_VERSION = "v6.1 by Duy Anh"
APP_NAME = f"{APP_TITLE} {APP_VERSION}"
BASE = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent
DATA_DIR = BASE / "Data"                # v5.9: mọi dữ liệu nằm trong Data\ -> lên bản mới chỉ cần chép thư mục Data
DATA_FILES = ("config.json", "owned.json", "history.json", "rclone.conf", "log.txt")


def _migrate_data_dir():
    """Chuyển dữ liệu kiểu cũ (nằm cạnh tool) vào thư mục Data. Không ghi đè file đã có trong Data."""
    try:
        DATA_DIR.mkdir(exist_ok=True)
    except OSError:
        return
    for name in DATA_FILES:
        old, new = BASE / name, DATA_DIR / name
        if old.is_file() and not new.exists():
            try:
                shutil.move(str(old), str(new))
            except OSError:
                try:
                    shutil.copy2(str(old), str(new))
                except OSError:
                    pass


_migrate_data_dir()
CONFIG_FILE = DATA_DIR / "config.json"
HISTORY_FILE = DATA_DIR / "history.json"
OWNED_FILE = DATA_DIR / "owned.json"        # Danh sách đã có (không tải)
RCLONE_CONF = DATA_DIR / "rclone.conf"      # cấu hình rclone (đăng nhập PikPak) -> đi theo thư mục Data
BACKUP_FILES = ("config.json", "owned.json", "history.json")
BACKUP_DIR = "_Backup FshareAutoDownload"
MACHINE = (os.environ.get("COMPUTERNAME") or "").strip() or __import__("socket").gethostname()
PATH_KEYS = ("firefox_path", "watch_folders", "library_root")
MACHINE_KEYS = PATH_KEYS + ("organize", "pikpak_root", "web_on", "web_port", "start_with_windows")
LOG_FILE = DATA_DIR / "log.txt"

WEB_FOLDER_API = "https://www.fshare.vn/api/v3/files/folder"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0"

CLOSE_FF_CHOICES = ("Chỉ đóng tab Fshare", "Đóng hẳn Firefox", "Không đóng")
INTERVAL_CHOICES = ("15", "30", "45", "60", "90", "120")
IDM_WINDOW_CHOICES = ("Ẩn xuống khay hệ thống", "Thu nhỏ xuống taskbar", "Giữ nguyên")
STATUS_NEW = "Tập mới"
STATUS_OPENED = "Đã mở trên Firefox"
STATUS_DL = "Đang tải"
STATUS_DONE = "Xong"
STATUS_SKIP_DONE = "Đã tải xong trước đó"
STATUS_HAVE = "Đã có trong thư mục phim"
STATUS_FAIL = "Lỗi - IDM chưa nhận"
MARKED = "Đánh dấu thủ công"   # sent_at của tập bạn tự đánh dấu là đã có
MAX_TRIES = 3                 # tự thử lại tối đa 3 lần với tập lỗi
STALE_HOURS = 12              # "Đang tải" quá 12 giờ mà chưa có file -> cho tải lại
STALE_FOLDER_DAYS = 14        # thư mục 14 ngày không có tập mới -> cảnh báo
CATCHUP_MAX_DAYS = 14         # quét bù tối đa 14 ngày khi tool không chạy
WATCH_STATUSES = (STATUS_OPENED, STATUS_DL, "Đã gửi IDM")

# ----- màu giao diện (v5.7: Graphite & Teal)
C_BG = "#eef1f5"
C_CARD = "#ffffff"
C_ACCENT = "#0e9384"
C_ACCENT_HOVER = "#0b7a6e"
C_ACCENT_DIS = "#9bd5cd"
C_SOFT = "#ecf7f5"           # nền nhạt cho thanh công cụ
C_SOFT_BORDER = "#cfe9e4"
C_SEL = "#d3efe9"            # dòng đang chọn
C_HEAD = "#f5f7f9"           # tiêu đề bảng
C_TEXT = "#17212b"
C_MUTED = "#64707d"
C_BORDER = "#e1e5ea"
C_GREEN = "#16a34a"
C_ORANGE = "#d97706"
C_RED = "#dc2626"
C_BLUE = "#2563eb"
C_SIDE = "#101418"
C_SIDE_CARD = "#171c22"
C_SIDE_HOVER = "#1d242c"
C_SIDE_ACTIVE = "#5eead4"
SIDE_W = 430          # thanh bên trái (mở rộng)
SIDE_W_MINI = 64      # thanh bên trái (thu gọn)
SIDE_MIN, SIDE_MAX = 240, 700   # giới hạn khi kéo đổi độ rộng
C_SIDE_TEXT = "#d0d7de"
C_SIDE_MUTED = "#6e7a86"
FONT = "Segoe UI"

ICON64 = "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"
ICON28 = "iVBORw0KGgoAAAANSUhEUgAAABwAAAAcCAYAAAByDd+UAAAFBElEQVR42qWWS2hcVRjHf9+5585MHrVprNaFj4WCj9ZHNRqKKCpatPgE4wMRFHRhBRHBdezGTRe2LhIfrWJLu4hoXRQFRQQ3Ki01C61Iq6jEvg2ZTO7M3Ln3fJ+LO0mTmqRpPfAxzJ1zzu98//P/vjsCCIODwqZNuvLLXRuM6FVU+4FlqAIinNMwwzmAGs79IIQtp9Y/8zmDg45Nm0wYGYl44onQu3fnkKuUX0IVa2VgCpwj6zQUxCGlGJxDm+nw+IPPbmRkJBKA3j3bhqLe3pd0fEIBEHELbeVE5hwjmC2WrAK43h4XxseHxx97YaOsGHn//qi74wtrNHOMCFk4LUFI8mwGIkCXjxcX3TCEIB0VH6YaD3hBX6eVGyEIJgI2r5QikKvS33sRy0vlIjtV9v1znCzoAue0YqWY0MpN0Nc9eei30BRMo8XuTERoZCmb195O38pVAGQauGbPDv7JW8TiWFhciyw0wbTfo9Zllp/dBiIQlCTP0LakU1mGhYDlAXO2CBBAQaTLk+W22L3N0TQEnBXGmTYQIRRhthTzmkd1ad5vZ8iZeYRQPDdbShmJJw8sGZiHuTyjgOWBdrGfdXjCwkAnclo+wKshZxC9gbfi0zDUbOaO5wXaAkAnQq3VIs2zwtkiWNqiNUsRM+NErUa1niBRhJlS9jHdpdKCUF/cy5kwqGcZj1+9mr5LLiXJWsTOkYbAlT0rZuZVfMwb6+6ikWeYGZ1xif3Hxvj015/pjGPUliipAd5g9MgYb95xH5cv75n3tBXvebVv3cz3v6oTbDvwfSHvAt5w5IEzw/JACeHQyRP0bdvK/iNjADTz/D+1loaihvcfGaNv21YOnTxBCSmA84SbqaNZIVo4b0VcJqk3eHj3dkaP/k3Fe4KevoKgSjny/HLyOA/v2k5Sb9DjS8XGasy3tyMopyMgQUnTlGoyRTWZoivyVKem2PDRu4weHcM7R66KmhE5R7XZ4PlPdnNqcpJuH5M0G1STKZJGHWnvOZvhLASmQ9RopilX9fTy8dPP8dDVq6kmCd0+ZiJJ2PDBO4weKaBOhGqzyUMfvcf+P/9gRblMrV6fszZpNBA1ZjPm3KFTpV6v8+SatTx63Q28su5OJCiWB5b5mFq9zoPbhzh4/BitEHjkw2G++/0wKzs6IShps8lT10+vvYOQ5UWGsxhzXWqF7qZKUKXRSkmTKSbaDdu7iL8nq6wf3sKqZcs48NshKl3dnKpO4F2E1mo0Wy2CKq08R9Ta3cnmlIUVfctmWpWpIiL0dnZx7zWr6YxLaPv3yDnSPCfXwCNrby1MJBCJo9qoc+WFK4vuZAYhhxDN6rPWbt7TD0wgy+mKY5wIt1x2BV+9/Np5/au5oFxpA+M20EBEvGUhEZEuTFFnxM7x1cGfGLj5Vjri0vREONvbR2iLVEz8bPQAlgXwCqoggpkmcsHLL37tSvHdlmUKRCJCM8vo6eyk7D12jpkJoGacrNUoez9TshLHTlvZNx7TzWi4h6BWtGOkwzmSep2aGec7KlGEFe9PAzGiIJhujtJ9Px4u37hmlXRU+klbhpqZmTgg+h9haqCqqCEdlcgajeHJdz7cGjEwEKU7d+8tr7l2lUTRbQJCaOuudv5hIM6JOCeWNoer7+/YyMBAFHHwIAwOuvStt/fGN12/T9QuNg0XiVFCFcwKFy811AwDM61hfKumr0xu37mFwUHH0JD9C9vwT36FYsyzAAAAAElFTkSuQmCC"

FIREFOX_CANDIDATES = [
    r"C:\Program Files\Mozilla Firefox\firefox.exe",
    r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Mozilla Firefox\firefox.exe"),
]


def find_firefox():
    for p in FIREFOX_CANDIDATES:
        if p and os.path.isfile(p):
            return p
    return shutil.which("firefox") or ""


def find_idm_folders():
    """Đoán thư mục lưu file của IDM (registry), mặc định là thư mục Downloads."""
    found = []
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\DownloadManager") as k:
                for name in ("LocalPathW", "LocalPath", "DefaultDir", "TempPath2"):
                    try:
                        v, _ = winreg.QueryValueEx(k, name)
                        if name != "TempPath2" and isinstance(v, str) and os.path.isdir(v):
                            found.append(v)
                    except OSError:
                        pass
        except OSError:
            pass
    dl = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(dl):
        found.append(dl)
    out = []
    for p in found:
        p = os.path.normpath(p)
        if p.lower() not in [x.lower() for x in out]:
            out.append(p)
    return "; ".join(out)


DEFAULT_CONFIG = {
    "firefox_path": "",
    "delay": 4,
    "idm_autoclick": True,
    "idm_timeout": 25,
    "close_firefox": "Chỉ đóng tab Fshare",
    "minimize_idm": True,
    "idm_window": "Ẩn xuống khay hệ thống",
    "window": {},
    "auto_scan": False,
    "auto_interval": 60,
    "notify": True,
    "tray_on_close": True,
    "start_with_windows": False,
    "startup_scan": True,
    "organize": False,
    "library_root": "",
    "check_local": True,
    "web_on": True,
    "web_port": 8080,
    "update_repo": "laocuumon1982-cpu/FshareAutoDownload",
    "update_check": True,
    "side_collapsed": False,
    "web_pin": "",
    "backup_on": True,
    "pikpak_user": "",
    "pikpak_pass": "",
    "profiles": {},
    "pikpak_on": False,
    "pikpak_remote": "pikpak",
    "pikpak_root": "Phim Hoat Hinh",
    "rclone_path": "",
    "catch_up": True,
    "watch_folders": "",
    "ff_warmup": 8,
    "watch_ff_downloads": True,
    "days_back": 0,
    "extensions": ".mkv .mp4 .avi .ts .m4v .mov",
    "folders": [],
}


# ----------------------------------------------------------------- tiện ích
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(default, dict) and isinstance(data, dict):
            merged = dict(default)
            merged.update(data)
            return merged
        return data
    except (OSError, ValueError):
        return json.loads(json.dumps(default))


def save_json(path, data):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def get_code(url):
    url = (url or "").strip()
    m = re.search(r"fshare\.vn/(?:folder|file)/([A-Za-z0-9]+)", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9]{6,}", url):
        return url
    return None


def to_date(value):
    if value in (None, "", 0, "0"):
        return None
    try:
        v = float(value)
        if v > 1e12:
            v /= 1000
        return dt.datetime.fromtimestamp(v).date()
    except (TypeError, ValueError, OSError):
        pass
    s = str(value)[:19]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%Y-%m-%d", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def human_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def name_key(name):
    """Chuẩn hóa tên file để so khớp (IDM có thể thay ký tự đặc biệt)."""
    return re.sub(r"[^0-9a-z]+", "", strip_accents(str(name)))


def folder_link(code):
    return f"https://www.fshare.vn/folder/{code}" if code else ""


def sub_label(sub, name):
    """Tên hiển thị kèm thư mục con: 'Season 2 › Tap 01.mp4'."""
    return f"{sub}  ›  {name}" if sub else name


def strip_accents(text):
    """Bỏ dấu tiếng Việt + chữ thường, để lọc từ khóa không phân biệt dấu/hoa thường."""
    text = unicodedata.normalize("NFD", str(text).replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in text if unicodedata.category(c) != "Mn").lower()


def one_line(text):
    """Ghi chú nhiều dòng -> 1 dòng để hiện trong bảng."""
    return " · ".join(x.strip() for x in str(text or "").splitlines() if x.strip())


def clean_paste(text):
    """Bỏ ký tự Tk không hiển thị được (emoji ngoài BMP) để dán không bị lỗi."""
    return "".join(ch for ch in str(text) if ord(ch) <= 0xFFFF).replace("\r\n", "\n").replace("\r", "\n")


def read_clipboard_windows():
    """Đọc clipboard bằng Windows API (ổn định hơn Tk khi có tiếng Việt / emoji)."""
    import ctypes
    from ctypes import wintypes
    u32, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u32.GetClipboardData.restype = ctypes.c_void_p
    k32.GlobalLock.restype = ctypes.c_void_p
    k32.GlobalLock.argtypes = [ctypes.c_void_p]
    k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    if not u32.OpenClipboard(None):
        return None
    try:
        h = u32.GetClipboardData(13)  # CF_UNICODETEXT
        if not h:
            return None
        ptr = k32.GlobalLock(h)
        if not ptr:
            return None
        try:
            return ctypes.wstring_at(ptr)
        finally:
            k32.GlobalUnlock(h)
    finally:
        u32.CloseClipboard()


def natural_key(text):
    """Sắp xếp 'Tap 2' trước 'Tap 10'."""
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", strip_accents(text))]


def split_keywords(text):
    return [strip_accents(k).strip() for k in re.split(r"[,;]", text or "") if k.strip()]


def match_keywords(name, include, exclude):
    n = strip_accents(name)
    inc, exc = split_keywords(include), split_keywords(exclude)
    if inc and not any(k in n for k in inc):
        return False
    return not any(k in n for k in exc)


def safe_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(name)).strip().rstrip(".")
    return name or "_"


def local_dir_for(cfg, folder_cfg, fname):
    """Thư mục phim trên máy của 1 thư mục Fshare: ô 'Thư mục phim trên máy' nếu có,
    không thì <Thư mục phim>\\<Tên phim>."""
    d = from_portable((folder_cfg or {}).get("local_dir", "").strip())
    if d:
        return d
    root = from_portable(cfg.get("library_root", "").strip())
    return os.path.join(root, safe_name(fname)) if root and fname else ""


def index_local_files(dirs, depth=3):
    """{khóa tên: [(đường dẫn, dung lượng, đuôi)]} của các file trong các thư mục (tối đa 3 cấp)."""
    idx = {}
    for root in dirs:
        if not root or not os.path.isdir(root):
            continue
        base = root.rstrip("\\/").count(os.sep)
        for cur, sub, files in os.walk(root):
            if cur.count(os.sep) - base >= depth:
                sub[:] = []
            for fn in files:
                stem, ext = os.path.splitext(fn)
                try:
                    size = os.path.getsize(os.path.join(cur, fn))
                except OSError:
                    continue
                rec = (os.path.join(cur, fn), size, ext.lower())
                keys = {name_key(stem), name_key(re.sub(r"(_\d+|\s*\(\d+\))$", "", stem))}
                for k in keys:
                    if k:
                        idx.setdefault(k, []).append(rec)
    return idx


def find_local(idx, name, size):
    """Trả về đường dẫn nếu tập đã có trên máy (cùng tên, cùng đuôi, đủ dung lượng)."""
    stem, ext = os.path.splitext(name)
    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    for path, fsize, fext in idx.get(name_key(stem), []):
        if fext == ext.lower() and (not size or fsize >= size * 0.995):
            return path
    return None


def copy_to_library(path, dest_dir):
    """Copy file đã tải xong vào thư mục phim (file gốc vẫn nằm trong thư mục IDM). Trả về đường dẫn bản copy."""
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, os.path.basename(path))
    if os.path.normcase(os.path.abspath(path)) == os.path.normcase(os.path.abspath(dest)):
        return path
    size = os.path.getsize(path)
    if os.path.exists(dest) and os.path.getsize(dest) == size:
        return dest          # đã copy trước đó rồi
    stem, ext = os.path.splitext(dest)
    n = 2
    while os.path.exists(dest):
        cand = f"{stem} ({n}){ext}"
        if os.path.exists(cand) and os.path.getsize(cand) == size:
            return cand
        dest = cand
        n += 1
    last = None
    for _ in range(5):  # IDM có thể còn giữ file vài giây
        try:
            tmp = dest + ".part"
            shutil.copy2(path, tmp)
            os.replace(tmp, dest)
            return dest
        except OSError as e:
            last = e
            try:
                os.remove(dest + ".part")
            except OSError:
                pass
            time.sleep(2)
    raise last


move_to_library = copy_to_library   # tên cũ


# ----------------------------------------------------------------- Ổ di động / hồ sơ máy / bảo mật nhẹ
def tool_drive():
    return os.path.splitdrive(str(BASE))[0]          # VD "E:" (ổ đang chứa tool)


def to_portable(value):
    """Đường dẫn nằm cùng ổ với tool -> lưu dạng @TOOL@\\... (đổi ký tự ổ vẫn đúng)."""
    out = []
    for part in str(value or "").split(";"):
        part = part.strip()
        drv, rest = os.path.splitdrive(part)
        td = tool_drive()
        out.append("@TOOL@" + rest if (drv and td and drv.upper() == td.upper()) else part)
    return "; ".join(x for x in out if x)


def from_portable(value):
    return str(value or "").replace("@TOOL@", tool_drive())


_SECRET_KEY = b"FshareAutoDownload-DuyAnh"


def enc_secret(text):
    """Mã hóa nhẹ (không để mật khẩu dạng chữ thường trong file cấu hình)."""
    import base64
    if not text:
        return ""
    data = text.encode("utf-8")
    return "x1:" + base64.urlsafe_b64encode(bytes(b ^ _SECRET_KEY[i % len(_SECRET_KEY)]
                                                  for i, b in enumerate(data))).decode()


def dec_secret(text):
    import base64
    if not text or not str(text).startswith("x1:"):
        return text or ""
    try:
        data = base64.urlsafe_b64decode(str(text)[3:].encode())
        return bytes(b ^ _SECRET_KEY[i % len(_SECRET_KEY)] for i, b in enumerate(data)).decode("utf-8")
    except Exception:
        return ""


def make_backup_zip(dest):
    """Nén các file dữ liệu quan trọng vào 1 file zip."""
    import zipfile
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        for name in BACKUP_FILES:
            fp = DATA_DIR / name
            if fp.is_file():
                z.write(fp, name)
        z.comment = f"{APP_NAME} | {MACHINE} | {dt.datetime.now():%Y-%m-%d %H:%M}".encode("utf-8")
    return dest


# ----------------------------------------------------------------- PikPak (qua rclone)
RCLONE_EXE = BASE / ("rclone.exe" if os.name == "nt" else "rclone")
RCLONE_ZIP_URL = "https://downloads.rclone.org/rclone-current-windows-amd64.zip"


def find_rclone(pref=""):
    """v5.8: rclone.exe LUÔN nằm cùng thư mục tool (đi theo ổ di động), không chọn đường dẫn nữa."""
    for c in (RCLONE_EXE, DATA_DIR / RCLONE_EXE.name):
        if c.is_file():
            return str(c)
    return ""


def download_rclone(progress=None):
    """Tải rclone.exe (bản Windows 64-bit) từ rclone.org về thư mục tool."""
    import tempfile
    import zipfile
    tmp = Path(tempfile.gettempdir()) / "rclone_fad.zip"
    with requests.get(RCLONE_ZIP_URL, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done * 100 // total)
    with zipfile.ZipFile(tmp) as z:
        name = next((n for n in z.namelist() if n.lower().endswith("/rclone.exe") or n.lower() == "rclone.exe"), None)
        if not name:
            raise RuntimeError("Không thấy rclone.exe trong file tải về")
        part = Path(str(RCLONE_EXE) + ".part")
        with z.open(name) as src, open(part, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.replace(part, RCLONE_EXE)
    try:
        tmp.unlink()
    except OSError:
        pass
    return str(RCLONE_EXE)


def run_rclone(rclone, args, timeout=60):
    """Chạy rclone ẩn cửa sổ. Trả về (mã thoát, stdout, stderr)."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        p = subprocess.run([rclone, "--config", str(RCLONE_CONF)] + list(args), capture_output=True, text=True,
                           encoding="utf-8",
                           errors="replace", timeout=timeout, creationflags=flags)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Quá thời gian chờ"
    except OSError as e:
        return -1, "", str(e)


def pikpak_join(*parts):
    return "/".join(str(x).strip().strip("/\\") for x in parts if str(x).strip().strip("/\\"))


def pikpak_dest(cfg, folder_cfg, fname):
    """remote:<Thư mục gốc>/<Tên phim>"""
    sub = (folder_cfg or {}).get("pikpak_dir", "").strip() or safe_name(fname or "Khác")
    return f"{cfg.get('pikpak_remote', 'pikpak') or 'pikpak'}:{pikpak_join(cfg.get('pikpak_root', ''), sub)}"


def startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.isfile(pyw):
        pyw = sys.executable
    return f'"{pyw}" "{os.path.abspath(__file__)}" --startup'


def set_startup(enabled):
    """Bật/tắt khởi động cùng Windows (HKCU\\...\\Run)."""
    if os.name != "nt":
        return
    import winreg
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, "FshareAutoDownload", 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(k, "FshareAutoDownload")
            except FileNotFoundError:
                pass


# ----------------------------------------------------------------- Fshare (đọc thư mục công khai)
class FshareError(Exception):
    pass


class FshareFolder:
    def __init__(self, log=None):
        self.log = log or (lambda m: None)
        self.web = requests.Session()
        self.web.headers.update({"User-Agent": BROWSER_UA, "Accept": "application/json"})
        self._warned_date = False
        self.errors, self.subs, self.subdirs = [], {}, []

    def _get(self, code, page):
        """Gọi API có thử lại (Fshare hay trả 429/5xx khi quét nhiều thư mục con liên tục)."""
        last = None
        for n in range(5):
            try:
                r = self.web.get(WEB_FOLDER_API, params={"linkcode": code, "sort": "type,name", "page": page},
                                 timeout=30)
            except requests.RequestException as e:
                last = FshareError(f"Lỗi mạng: {e}")
                time.sleep(2 + n * 2)
                continue
            if r.status_code == 404:
                raise FshareError("Thư mục không tồn tại hoặc đã bị xóa")
            if r.status_code in (429, 500, 502, 503, 504):
                last = FshareError(f"Fshare bận (HTTP {r.status_code})")
                time.sleep(3 + n * 3)
                continue
            if r.status_code != 200:
                raise FshareError(f"Không đọc được thư mục (HTTP {r.status_code})")
            try:
                return r.json()
            except ValueError:
                last = FshareError("Fshare trả dữ liệu lỗi")
                time.sleep(2)
        raise last

    def list_folder(self, code, info=None):
        """Đọc HẾT các trang của 1 thư mục. Không tin vào _links/_meta (Fshare có lúc không trả) -
        cứ đọc trang kế tiếp cho tới khi gặp trang rỗng hoặc trang lặp lại."""
        items, page, folder_name, seen = [], 1, None, set()
        while page < 2000:
            j = self._get(code, page)
            if folder_name is None:
                cur = j.get("current") or {}
                folder_name = cur.get("name") if isinstance(cur, dict) else None
            batch = j.get("items") or []
            fresh = []
            for it in batch:
                key = it.get("linkcode") or it.get("id") or json.dumps(it, sort_keys=True, default=str)
                if key not in seen:
                    seen.add(key)
                    fresh.append(it)
            if info is not None:
                info.append((page, len(batch), len(fresh), sorted(j.keys()), j.get("_meta"), (j.get("_links") or {}).get("next")))
            if not fresh:          # trang rỗng / lặp lại trang trước -> hết
                break
            items.extend(fresh)
            page += 1
        return folder_name, items

    @staticmethod
    def code_of(it):
        """Mã (linkcode) của file / thư mục con; dự phòng lấy từ link nếu thiếu trường linkcode."""
        c = it.get("linkcode") or it.get("link_code") or it.get("code")
        if c:
            return str(c)
        for k in ("furl", "url", "link", "path_link"):
            m = re.search(r"/(?:folder|file)/([A-Za-z0-9]+)", str(it.get(k) or ""))
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def is_dir(it):
        return str(it.get("type")).lower() in ("0", "folder", "dir")

    def walk(self, code, sub="", _seen=None, _depth=0):
        """Duyệt hết file trong thư mục + mọi thư mục con. Mỗi file có thêm '_sub' = đường dẫn thư mục con.
        Thư mục con đọc lỗi thì ghi vào self.errors và đi tiếp (không làm mất các thư mục khác)."""
        if _seen is None:
            _seen = set()
            self.errors, self.subs, self.subdirs = [], {}, []
        if code in _seen or _depth > 12:
            return
        _seen.add(code)
        try:
            _, items = self.list_folder(code)
        except Exception as e:
            if _depth == 0:
                raise
            self.errors.append((sub, str(e)))
            self.log(f"   ⚠ Thư mục con \"{sub}\" đọc lỗi: {e}")
            return
        nfile = 0
        for it in items:
            if self.is_dir(it):
                cc = self.code_of(it)
                child = (sub + " › " if sub else "") + (it.get("name") or cc or "?")
                if cc:
                    self.subdirs.append({"path": child, "code": cc})
                    yield from self.walk(cc, child, _seen, _depth + 1)
                else:
                    self.errors.append((child, "không có mã thư mục"))
            else:
                if not it.get("linkcode"):
                    cc = self.code_of(it)
                    if cc:
                        it["linkcode"] = cc
                it["_sub"] = sub
                nfile += 1
                yield it
        self.subs[sub] = nfile

    def item_date(self, it):
        d = to_date(it.get("created")) or to_date(it.get("modified")) or to_date(it.get("created_at"))
        if d is None and not self._warned_date:
            self._warned_date = True
            self.log(f"[!] Không đọc được ngày của file. Các trường có: {', '.join(sorted(it.keys()))}")
        return d


# ----------------------------------------------------------------- CẬP NHẬT
def ver_tuple(v):
    """'v5.9 by Duy Anh' / 'v6.0' -> (5, 9) để so sánh phiên bản."""
    return tuple(int(x) for x in re.findall(r"\d+", str(v).split(" by")[0])[:4]) or (0,)


def file_version(py_path):
    try:
        m = re.search(r'^APP_VERSION = "([^"]+)"', Path(py_path).read_text(encoding="utf-8", errors="ignore"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def github_latest(repo):
    """Thông tin bản phát hành mới nhất trên GitHub: (tag, ghi chú, url file zip phù hợp)."""
    r = requests.get(f"https://api.github.com/repos/{repo.strip().strip('/')}/releases/latest", timeout=20,
                     headers={"Accept": "application/vnd.github+json", "User-Agent": "FshareAutoDownload"})
    if r.status_code == 404:
        raise RuntimeError(f"Chưa có bản phát hành nào ở github.com/{repo} (hoặc repo không tồn tại / riêng tư).")
    r.raise_for_status()
    j = r.json()
    zips = [a for a in j.get("assets") or [] if str(a.get("name", "")).lower().endswith(".zip")]
    frozen = getattr(sys, "frozen", False)
    pick = None
    for a in zips:
        n = a["name"].lower()
        if frozen and "portable" in n:
            pick = a
        elif not frozen and "portable" not in n:
            pick = pick or a
    url = (pick or {}).get("browser_download_url") or (None if frozen and zips else j.get("zipball_url"))
    if not url and zips:
        url = zips[0]["browser_download_url"]
    return j.get("tag_name") or j.get("name") or "", j.get("body") or "", url


def download_file(url, dest, progress=None):
    with requests.get(url, stream=True, timeout=60, headers={"User-Agent": "FshareAutoDownload"}) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length") or 0)
        done = 0
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
                done += len(chunk)
                if progress:
                    progress(done * 100 // total if total else -1, done)
    return dest


UPDATE_SKIP = {"data", "__pycache__", ".git", "_bancu"}


def stage_update(zip_path):
    """Giải nén bản cập nhật, chép đè vào thư mục tool - GIỮ NGUYÊN thư mục Data.
    Trả về (phiên bản mới, đường dẫn exe mới cần thay sau khi thoát hoặc None)."""
    import tempfile
    import zipfile
    tmp = Path(tempfile.mkdtemp(prefix="fad_upd_"))
    with zipfile.ZipFile(zip_path) as z:
        for n in z.namelist():
            if n.startswith("/") or ".." in Path(n).parts:
                raise RuntimeError("File cập nhật không hợp lệ")
        z.extractall(tmp)
    exe_name = Path(sys.executable).name if getattr(sys, "frozen", False) else "FshareAutoDownload.exe"
    root = None
    for cur, dirs, files in os.walk(tmp):
        dirs[:] = [d for d in dirs if d.lower() not in UPDATE_SKIP]
        if "fshare_auto_idm.py" in files or exe_name in files or "FshareAutoDownload.exe" in files:
            root = Path(cur)
            break
    if root is None:
        raise RuntimeError("File zip không phải bản Fshare Auto Download (không thấy fshare_auto_idm.py / .exe).")
    frozen = getattr(sys, "frozen", False)
    new_ver = file_version(root / "fshare_auto_idm.py") if (root / "fshare_auto_idm.py").is_file() else ""
    has_exe = (root / "FshareAutoDownload.exe").is_file() or (root / exe_name).is_file()
    if frozen and not has_exe and not new_ver:
        raise RuntimeError("Bản cập nhật không có FshareAutoDownload.exe hay fshare_auto_idm.py.")
    old_dir = BASE / "_BanCu"
    old_dir.mkdir(exist_ok=True)
    cur_py = BASE / "fshare_auto_idm.py"
    if cur_py.is_file():
        shutil.copy2(cur_py, old_dir / f"fshare_auto_idm_{file_version(cur_py).split(' by')[0] or 'cu'}.py")
    pending_exe = None
    for item in root.iterdir():
        if item.name.lower() in UPDATE_SKIP or item.name in DATA_FILES:
            continue
        dest = BASE / item.name
        if item.is_dir():
            shutil.copytree(item, dest, dirs_exist_ok=True)
        elif frozen and item.name in (exe_name, "FshareAutoDownload.exe"):
            pending_exe = BASE / (exe_name + ".new")
            shutil.copy2(item, pending_exe)
        else:
            shutil.copy2(item, dest)
    shutil.rmtree(tmp, ignore_errors=True)
    return new_ver, pending_exe


def run_newer_script():
    """Bản exe: nếu cạnh exe có fshare_auto_idm.py MỚI HƠN (do bấm Cập nhật) thì chạy code mới đó
    bằng Python có sẵn trong exe -> cập nhật được cả bản exe mà không cần đóng gói lại."""
    if not getattr(sys, "frozen", False) or "--embedded" in sys.argv:
        return False
    py = BASE / "fshare_auto_idm.py"
    if not py.is_file() or ver_tuple(file_version(py)) <= ver_tuple(APP_VERSION):
        return False
    src = py.read_text(encoding="utf-8")
    g = {"__name__": "__main__", "__file__": str(py), "__builtins__": __builtins__}
    exec(compile(src, str(py), "exec"), g)
    return True


# ----------------------------------------------------------------- Firefox
def open_in_firefox(firefox, url):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.Popen([firefox, "-new-tab", url], creationflags=flags)


def firefox_download_dirs():
    """Thư mục Firefox tự lưu file (khi IDM không bắt được link): Downloads của Windows
    + thư mục riêng đặt trong Firefox (browser.download.dir) nếu có."""
    dirs = []
    try:
        if os.name == "nt":
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                v, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
                dirs.append(os.path.expandvars(v))
    except OSError:
        pass
    dirs.append(os.path.join(os.path.expanduser("~"), "Downloads"))
    try:
        prof_root = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
        for prof in os.listdir(prof_root):
            prefs = os.path.join(prof_root, prof, "prefs.js")
            if not os.path.isfile(prefs):
                continue
            txt = open(prefs, encoding="utf-8", errors="ignore").read()
            m = re.search(r'user_pref\("browser\.download\.dir",\s*"(.+?)"\);', txt)
            if m:
                dirs.append(m.group(1).replace("\\\\", "\\"))
    except OSError:
        pass
    out = []
    for d in dirs:
        if d and os.path.isdir(d) and os.path.normcase(os.path.abspath(d)) not in \
                [os.path.normcase(os.path.abspath(x)) for x in out]:
            out.append(d)
    return out


def firefox_grabbed(dirs, name, since):
    """True nếu Firefox đang/đã tự tải file này (có <tên>.part hoặc file mới xuất hiện sau thời điểm since)."""
    stem = name_key(os.path.splitext(name)[0])
    for d in dirs:
        try:
            for fn in os.listdir(d):
                base = fn[:-5] if fn.lower().endswith(".part") else fn
                if name_key(os.path.splitext(base)[0]) != stem and \
                        name_key(re.sub(r"(_\d+|\s*\(\d+\))$", "", os.path.splitext(base)[0])) != stem:
                    continue
                try:
                    if os.path.getmtime(os.path.join(d, fn)) >= since - 5:
                        return os.path.join(d, base)
                except OSError:
                    pass
        except OSError:
            pass
    return None


# ----------------------------------------------------------------- IDM: tự bấm "Bắt đầu tải"
START_BTN_RE = re.compile(r"(start\s*download|bắt\s*đầu\s*tải)", re.I)
IDM_DIALOG_RE = re.compile(r"(download file info|thông tin)", re.I)


class IdmClicker:
    """Tìm hộp thoại tải của IDM rồi bấm nút 'Bắt đầu tải' (Start Download); không có nút thì nhấn Enter."""

    def __init__(self):
        self.ok = os.name == "nt"
        self.done = set()
        if not self.ok:
            return
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.u32 = ctypes.windll.user32
        self.k32 = ctypes.windll.kernel32
        self.ENUM = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _text(self, hwnd):
        buf = self.ct.create_unicode_buffer(512)
        self.u32.GetWindowTextW(hwnd, buf, 512)
        return buf.value

    def _cls(self, hwnd):
        buf = self.ct.create_unicode_buffer(256)
        self.u32.GetClassNameW(hwnd, buf, 256)
        return buf.value

    def _exe(self, hwnd):
        pid = self.wt.DWORD()
        self.u32.GetWindowThreadProcessId(hwnd, self.ct.byref(pid))
        h = self.k32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ""
        try:
            buf = self.ct.create_unicode_buffer(1024)
            size = self.wt.DWORD(1024)
            if self.k32.QueryFullProcessImageNameW(h, 0, buf, self.ct.byref(size)):
                return os.path.basename(buf.value).lower()
            return ""
        finally:
            self.k32.CloseHandle(h)

    def _idm_dialogs(self):
        found = []

        def cb(hwnd, _):
            if (self.u32.IsWindowVisible(hwnd) and not self.u32.IsIconic(hwnd) and hwnd not in self.done
                    and self._cls(hwnd) == "#32770" and self._exe(hwnd) == "idman.exe"):
                found.append(hwnd)
            return True
        self.u32.EnumWindows(self.ENUM(cb), 0)
        return found

    def _windows_of(self, exe, cls=None):
        found = []

        def cb(hwnd, _):
            if self.u32.IsWindowVisible(hwnd) and (cls is None or self._cls(hwnd) == cls) \
                    and self._exe(hwnd) == exe:
                found.append(hwnd)
            return True
        self.u32.EnumWindows(self.ENUM(cb), 0)
        return found

    def minimize_progress(self, wait=5.0, hide=True):
        """Ẩn cửa sổ tiến trình tải của IDM xuống khay hệ thống (hide=True) hoặc thu nhỏ xuống taskbar.
        Muốn xem lại: mở IDM từ biểu tượng khay, nhấp đúp tập đang tải."""
        if not self.ok:
            return 0
        sw = 0 if hide else 6  # SW_HIDE / SW_MINIMIZE
        end, n = time.time() + wait, 0
        while time.time() < end:
            wins = self._idm_dialogs()
            for h in wins:
                if START_BTN_RE.search(" ".join(self._child_texts(h))):
                    continue  # hộp thoại tải tập khác đang chờ bấm, không thu nhỏ
                self.u32.ShowWindow(h, sw)
                self.done.add(h)
                n += 1
            if n:
                time.sleep(0.6)  # IDM có thể hiện thêm 1 cửa sổ nữa
                for h in self._idm_dialogs():
                    if not START_BTN_RE.search(" ".join(self._child_texts(h))):
                        self.u32.ShowWindow(h, sw)
                        self.done.add(h)
                return n
            time.sleep(0.3)
        return n

    def _child_texts(self, hwnd):
        out = []

        def cb(h, _):
            if self._cls(h) == "Button":
                out.append(self._text(h).replace("&", ""))
            return True
        self.u32.EnumChildWindows(hwnd, self.ENUM(cb), 0)
        return out

    # ----- cửa sổ đang dùng / Firefox
    def get_foreground(self):
        return self.u32.GetForegroundWindow() if self.ok else None

    def focus(self, hwnd):
        if not self.ok or not hwnd or not self.u32.IsWindow(hwnd):
            return False
        if self.u32.IsIconic(hwnd):
            self.u32.ShowWindow(hwnd, 9)
        self.u32.keybd_event(0x12, 0, 0, 0)  # ALT: Windows mới cho đưa cửa sổ khác lên trước
        self.u32.SetForegroundWindow(hwnd)
        self.u32.keybd_event(0x12, 0, 2, 0)
        time.sleep(0.35)
        return self.u32.GetForegroundWindow() == hwnd

    def firefox_titles(self):
        if not self.ok:
            return {}
        return {h: self._text(h) for h in self._windows_of("firefox.exe", "MozillaWindowClass")}

    def close_fshare_tab(self, name, code, before):
        """Chỉ đóng tab Fshare mà tool vừa mở (Ctrl+W), không đụng các tab khác.
        Nhận biết qua tiêu đề cửa sổ Firefox: có mã file / tên file, hoặc vừa chuyển sang trang Fshare."""
        if not self.ok:
            return False
        stem_key = name_key(os.path.splitext(name)[0])[:14]
        for h, title in self.firefox_titles().items():
            t = title.lower()
            ours = (code.lower() in t
                    or (len(stem_key) >= 6 and stem_key in name_key(title))
                    or ("fshare" in t and "fshare" not in before.get(h, "").lower()))
            if not ours:
                continue
            if not self.focus(h):
                return False
            self.u32.keybd_event(0x11, 0, 0, 0)  # Ctrl
            self.u32.keybd_event(0x57, 0, 0, 0)  # W
            self.u32.keybd_event(0x57, 0, 2, 0)
            self.u32.keybd_event(0x11, 0, 2, 0)
            time.sleep(0.4)
            return True
        return False

    def close_firefox(self, wait=6.0):
        """Đóng Firefox nhẹ nhàng (WM_CLOSE), không đóng được thì tắt hẳn."""
        if not self.ok:
            return False
        wins = self._windows_of("firefox.exe", "MozillaWindowClass")
        for h in wins:
            self.u32.PostMessageW(h, 0x0010, 0, 0)  # WM_CLOSE
        end = time.time() + wait
        while time.time() < end:
            if not self._windows_of("firefox.exe", "MozillaWindowClass"):
                time.sleep(1.0)  # chờ tiến trình thoát hẳn trước khi mở lại
                return True
            time.sleep(0.4)
        subprocess.run(["taskkill", "/IM", "firefox.exe", "/F"], capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        time.sleep(1.5)
        return True

    def _start_button(self, dlg):
        btn = []

        def cb(hwnd, _):
            if self._cls(hwnd) == "Button" and self.u32.IsWindowVisible(hwnd) \
                    and START_BTN_RE.search(self._text(hwnd).replace("&", "")):
                btn.append(hwnd)
                return False
            return True
        self.u32.EnumChildWindows(dlg, self.ENUM(cb), 0)
        return btn[0] if btn else None

    def _press_enter(self, dlg):
        self.u32.ShowWindow(dlg, 9)  # SW_RESTORE
        self.u32.SetForegroundWindow(dlg)
        time.sleep(0.3)
        self.u32.keybd_event(0x0D, 0, 0, 0)
        self.u32.keybd_event(0x0D, 0, 2, 0)

    def click_once(self):
        if not self.ok:
            return None
        for dlg in self._idm_dialogs():
            btn = self._start_button(dlg)
            if btn:
                time.sleep(0.5)
                self.u32.PostMessageW(btn, 0x00F5, 0, 0)  # BM_CLICK
                self.done.add(dlg)
                return "button"
            if IDM_DIALOG_RE.search(self._text(dlg)):
                time.sleep(0.5)
                self._press_enter(dlg)
                self.done.add(dlg)
                return "enter"
        return None

    def wait_and_click(self, timeout, stop_flag):
        end = time.time() + timeout
        while time.time() < end and not stop_flag.is_set():
            r = self.click_once()
            if r:
                return r
            time.sleep(0.4)
        return None


# ----------------------------------------------------------------- Theo dõi tải xong
def scan_completed(watch_folders, pending):
    """pending: {code: (name, size)}. Trả về {code: đường dẫn file} cho những tập đã tải xong.
    IDM tải vào thư mục tạm, xong mới chuyển file sang thư mục lưu -> file xuất hiện đủ dung lượng là xong."""
    if not pending:
        return {}
    want = {}
    for code, info in pending.items():
        name, size = info[0], info[1]
        want.setdefault(name_key(os.path.splitext(name)[0]), []).append((code, size, os.path.splitext(name)[1].lower()))
    result = {}
    now = time.time()
    for root in watch_folders:
        if not os.path.isdir(root):
            continue
        base_depth = root.rstrip("\\/").count(os.sep)
        for cur, dirs, files in os.walk(root):
            if cur.count(os.sep) - base_depth >= 3:
                dirs[:] = []
            for fn in files:
                stem, ext = os.path.splitext(fn)
                key = name_key(stem)
                if key not in want:  # IDM có thể thêm hậu tố _2 / (1) khi trùng tên
                    key = name_key(re.sub(r"(_\d+|\s*\(\d+\))$", "", stem))
                cands = want.get(key)
                if not cands:
                    continue
                path = os.path.join(cur, fn)
                try:
                    st = os.stat(path)
                except OSError:
                    continue
                for code, size, want_ext in cands:
                    if code in result or (want_ext and ext.lower() != want_ext):
                        continue
                    try:
                        size = int(size or 0)
                    except (TypeError, ValueError):
                        size = 0
                    if (size and st.st_size >= size * 0.995) or (not size and now - st.st_mtime > 15):
                        result[code] = path
    return result


# ----------------------------------------------------------------- GUI
# ----------------------------------------------------------------- Máy chủ web (điều khiển từ điện thoại)
ICON180 = "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"
ICON512 = "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAYAAAD0eNT6AAA5uUlEQVR42u3daZBl533f99/zPOfcvdfpmcEsmBkMZrCRAAGBIlmmTJGEyMiwTadiW2FKJUt0ZCepUEyi2FKqkqowUflFqlKSF0qWSFCkSEigJdIVxTFLkuWyTJMWJYsiSHAHiEXYt5npmem7nnOevHjO7b493T3rXc699/tRtUAAg0Hj9D3n/3v+z3LM0s99yAvjYcz2P/eXufSlSCqVZKoVGWOkpQUpTWWqFflGVUpSyUgmjmXrNfk05foCGM+jzDllG035Xk/ykiInc7El32pLzknrF+S9D3/e7UrdZDjPRQxVxCUYU8H3fucH2xiZUixVy1K1IlOvydTKeeEvS9bKVCvhn4vjrd8jzaT8t/ZJKp+HAQAYB5+kMvWabOTyvyBp30p45hkj9XqSMSEAZJl8qyN1u/LNjvxGU2q1pVZHvtvbveBf+uwEAWC6iv4uBT+OZBo1qV6X3bckVasylVimUpbiWMbZUMyzLCTrLJNvdyQZqdMZ+I3s4K24M0EDwEifc5KSJDyvNmWXPKO8FDnJxrILjTCgiZx8mkm9nny7I9/uSa2WstfXpY0N+YtNqZdsf3aazf9HGCAAFLXo+5CCBz+gecE3y4vS0oJsoybTqEtxFH5dL5FPEvl2V2q15Qc+56G/bwaKu73MnQgAkxrsaI9nlJEyL6VJGNAMPh6NlZyTXSpLa8uyxw6H5+HFDWUXm2EK4dz5rUCgXQIBYYAAUKiib63MQl1mZUHatyJbrcos1EL7Pk3lO90wom+18g+x3RrBG8s1BTCjz0qzc7ySpqEb0GmH56ixUqUst7QgHTscugQXmspaLen1s/JnL8hf2JCybCsQEAYIAGNl81S7WfSNtLggd2BVWl2RWazJlMuhfd8Jc15Sa+uDuqPYM4IHMM8dBLv1GEwS+SRfWCgrVUpySw3p8EH5Tkf+fFM6c1bpK2ek8xfCs1h++7MZBICRfFC9Dx+wfFW+278irS7LLjakSlnqdPNRfnd7wR9sjfH5BIC9Hrb5czP/016ytdPAGNnFurR/Rfb4EWXnL0pnzil99ay0fmGr+BvWCxAAhlH0jbaN9k2tKnNov+yB1bCYr1KWOj35Tics0jN5kt02N8aHEACuOw8MBALf6UrtdggDSwvSgVXZEx35i01lr5yRf/FV+WZr65+3ZufaLBAArlj8fT6/b63M2rLMTQdk9y3KNBpSpxM+iJ1uXvQNK/EBYCwNArsjDJiFutzaqvyxm5S9fl7+pVfkXzuXrxcYeKaDALBn0Ze29tpXyrKHD8jetBZW8Uvyrbb8+rpkHEUfAAoTBnohDLhI7uhB6ehB+XPnlb30mrIXXpHanZ3PeRAAdnwgGjXZ44dk96+FNn+3Gw6ukA8fNuv41ABA4cKAk7yX39iQZGQaNbm7TskeO6zs1deUPfOidLFJECAADBT//vz+6rLsof0y+8P8vt9oyp8/vzXa9/lcEoN+ACim/u4Bc0lX4JZjsvvX5F89o+zFV+XPnNtRAwgA81b4vQ+F/8Rh2bV9UhzJX9yQX+8X/oFjLrd9wAAAxQ4CeVcg9eGZbp3siaOyR25S9trryp5+Ye6DwPwEgEvn+BtVuVuPy960P5xZ3WxJrUyy/Tb/5ieImwkApjUJ9KcH5POurpU9sCa7f5+yl15V+v1npIutnXWCADBjI35JplaRueWI7IF9MpXK9vn9zQN6GPIDwMy1BGy+aLAZ1gnYwwdkVheVvfK6/FPPyzfbc9URmP0A0P9Bukj2xCHZIzfJLDbkL1wMi0WMZZQPAPOkv3sgrwHulmPy+1aVPf+SsqdflNJkLkLA7AaAgXl+e3BN5tabZfethMK/vh7a/Jy/DwAEgfX1sFjwrtMyB9fkv/+sspdfm/luwOwFgIHCr3o+z3/4QDhj+ux6OBnKODr7AIC8brjw/paz67K1qnTfXTIvvBLWB2y0ZjYIzFYAGPgB2VuOyp44KlMu5fP8ZnP+BwCASwqIZE3YPtjphvUB+5aVPf2csqeeC7VlxkLAbASAwW19C3XZO07KHlgL7f6LGxR+AMBV5wDJbNYOd9dpmbUVZd95MryOeIa6AdMfAAZeARlG/Udk4iif57fh79PvBwBca22Rl19fl11ZlHnzG5U9/fxWN2AGXj88vQGgv18z8zKNquxdp2T37wuj/qTHqB8AMIQgYOVbrbBb4K5TMvtXlH3rCfmLrak/N2A6q+TgCv9D+xX94D2yK0vy587nx/VS/AEAw6o5VvKSP3dedmUp1JxD+7cWnE/pi+GmrwNg8rTlrOzpW+ROHAnp7GJza9RPxx8AMIpuwMWmFDm5N90pLS0qe/wpKc3y2kQAGPnI39Srsm84Lbu2mp/bzwp/AMB4QoDSTP7CRbmTN8ss1pV983H5jdbULQ6cjqppzFbxP7im6C33yC4thJa/tVPbfgEATKF80OnPnZddWlD0lntkDq5tTQdMSU0qfgDot/y9l731ZkX33CGfeflmU3KM+gEAE+KsfLMpn3lF99whe+vNA+sCiv/tF3sKoN9OiZzcHbfIHjsif+FCmGexVkz2AwAmO4y2UprItxO520/KVMtKv/OUlKSFnxIo7hC6f+HiSPaeO+RO3Bzm+31/OoDPHQBgwny/Xhn59fNyJ26WvecOKY4Kv0OgmB2AzcV+Nbn77pSplJW9fm6r5e+1/Y8AAEwyBEiSscpePye3uiT71jcp/eq3w1H0Be0EFK8D0L9Qi3W5H7grnOXPfD8AYBrk6wJMuST3A3dJi/XCdgKKVVXz4m8P7lP05rvDixnanfDqXgAApoF1ee0yit58t+zBfYUMAcUJAAPF391zp9RLthZRAAAwTYwJNayXyN1zZyFDQFSYC7VZ/O+QbzfDSxYML/IBAExrCJCUpfLtptw9d0hf/46yl18vzJqAyQeAS4t/q7VV/Kn9AIBpTwGpl2+1ChcCJjsFcLniDwDATGSA8OrgfggoynSAnegFofgDAAgBcxQA+sX/wCrFHwAwnyHgwOpEQ4CdyAXwXmpU5e46HbZKUPwBAPMUAtodubtOS43qxEKAHft/eH7CX/Smu+R9JqVs9QMAzFkISFN5nyl6010y9dpEQkA01v/g/ot97r5NphSF0f+UvT8ZAICh6PZkKmW5u29T8mePjf0FQuPpAPT/g5yTfeNpmWolzPsz8gcAzHEnwLdaMtWK7BtPS86NtRMw1ikAd+q43KGD8s0Wx/sCAGCdfLMld+ig3Knj4/1Xj2v0b48flj1xRP7sOi/2AQBgc3Rs5c+uy544Inv88Ni6AKOtxDZf9Le2InfquPz5i+GvAQCAbfXSn78od+q4zNpKCAEjrpejCwD5VgfVKnJ33irf7bHYDwCAvXgv3+3J3XmrVKuMfIv8aHYB9L9ha+VuPykTRfLNZpj3JwMAALB77ex2ZWo1udtPKv3ad7amA0YwgB7dNkDvZU8eld2/Kn/ufD7vT/UHAGBP1spvNEPtvOWIsu8/O7IuwPCnAPqH/exfkTt5TH79Aov+AAC4Ws7Kr1+QO3lMZv/KyBYFDrcy94t/rSJ3+61hux9r/gAAuMZ6qrA98PZbZWqVkYSA4QWA/jdmjOzpYzKlSEp6HPYDAMD11NSkJ1OKZE8f21Zjh2WIawC85CV75KDsgQPy6+dZ9AcAwHWHACd/sRlq6uF1Zc+/PNSu+nA6AMZIXjK1quzJY/IXLzLyBwBgCPXVX7woe/KYTK0aBtVDqq/RML65/h/trcdknJXvppJh1T8AADdWYyVlqYwry956TOk3vjcw8L6xGmuH8s15L3v4gOxNa2G/v2HVPwAAwwkBVr7ZlL1pTfbwgXxB4I3/tjdWqfut/2pZ9pYjofXPUb8AAAyXzacCbjkiUy0PZSrgxofq3ssePyITl6Q0E/v+AAAYehtASjOZuCR7/MhQTga8/jUA/T3/q0uyhw7Ib2yE1j/T/gAAjCADWPmNDdlDB5S98rr8mfUbWgtwfR2AftvBRSGJ9BJG/gAAjKMT0EtC7XXR9po8lgAgH1r/N+2TXVuVb3fY9gcAwMjrv5Fvd2TXVmVv2peP/q+vA3CdUwBGphzL3nxTvvBPovcPAMAYWIUFgTffJP/aWflu77pq8LV3AGyYb7BHDsg06hz3CwDAmLsASnoyjbrskXxb4HXswLPX/C/NvEylLHNwv/zFDck4fhgAAIw1BDj5ixsyB/fLVMpSdu0vC7rGKYDQYrDHDsuUS6z8BwBgUtJUpl6RPXZY6fee0rUW46vvAPQP/Vmoyx7aL7/BiX8AAEyuC2DlN5qyh/bLLNSv+XCga6jgIVmYwwe2/TkAAJiU66/NVxcANkf/DbmD++SbrTxlEAIAAJhY8TdGvtmSO7hPZqFxTV2Aq1sDkNd6u39Zipzks4H2PyEAAIDJ5YBMipzs/mWlFy5u1uwb7wAMrvw/sCp/scXCPwAACtIEkLHyF1syB1avaUfAVe8CsIfWZKo1+fPnJesY/AMAUBRpIlNflD20pvSp56+url9x9O+9TBTJrK3Ib7RY+Q8AQNEYK7/RkllbkYmicDjQFboAVwgA+R8Orso0GlLKqX8AABQvABgp7ck0GjIHV7fV8OsLAPnxgmZ1Wb7b3Wr9AwCAYrFOvtuVWV3ePLb/cqLLpgnvZZYasqtLYfEf3X8AAIqr3ZVdXVK22JA/d2Gzll9bAOgfLrC2kv/PTPIkAAAAiisLxwOsrYQAcJnV+vZy9d+US2H032qz+A8AgKIzVr7Vll1dkimXLrtbb/eq3l/8t29Zpl6T0pSLCgDANEhTmXpNZt/ytpp+9R0AY2RWluQ73et6zzAAAJgAa+Q7XZmVpcvu3LO7FX55ydQqsksNqdvTFfcSAACAgjBStye71JCpVfZ8P8CeHQC7vCSVSlKWcS0BAJgmWSaVSqGW72GXXQA+DPiXG/KdTkgSnjN/AQCYpi6A73Sk5Yb0Ql7bL9sB2Gz/V2UXF0L7n+4/AADTVv/DNMDigkytuus0wK5TAHZ5USrHtP8BAJhWWSaV41DTd6v12/80HP2r5YV89T97/wEAmErWhlq+vJDv5ts+DbC1BqB/9G+1LLvYkO/Q/gdGidtr29ADwCh0erKLDWWVsnyzve1o4B2LAE2jIcUlqd3lzX/AKCpdfv8lnik2SbLGyObrj0hFwLCfOV6KSzKNRggAu3YA+hl8qR5O/vM+/0vckcBw70ev2FgtlyrzXfTy//ZOmqqVJDL9EABgeDeZ96GmL9WlV17T4E0WAkC/JRA52WolvPrXXBIMANwwZ4zWu139yOFj+s0f+lFl3ofR7xxKfKbIWP2T73xN/8ejf6z9lRpdEWDYjOS7XdlqRVnkpCTdrPnbpgBMtSLTqMm3OrT/gREOfGNjtVQqczEk1aKIYQYwsgBgpCSVadRkqhX5CxuXdAD6v65Rk+JIarZ4+x8w4hDgJToAxirloDFgxA+cTIqjMMDfGQDyG7BRC+0B5v2B0QfzgS/++wGM9G5L0lDjB2p+tGP+v9PblgkAjGD4j60FkH7gmnBtgNHcbp3ejnUAm31+UymH4wL7CwQAAMAMNADydQC1qkxla+3R5hoAU61IsZNaGQEAoAUw5uvhL2kFABjubeal2IWFgBebkiTbPxHINKrhF1D7AQCYsS5ACAGmUd0MBGENgDFSpSL1Ekb/AADMXAAwocZXKuE9P97nHQBnpVIkn6SbZwQDAIAZ4X2o8aUo1Pz+QUCmFMtWKvL98//JAMAIb0Rxj+11TbguwKhaAFKSyVYqykqxfC/JdwGUSzJRJGXcfQAAzKTMh1pfLklSCACmUgpzAoYAAADAbDYBvGRtqPnqbwOsVOTTJP8VhABgtOh1731NuC7ASO+0NF8IuNkBcFbKMrYAAgAwsx0ASVkWar4kK2ulOFaWJIRvAABmdvivUOvjWLJWkeJYPnYySRbqPyEAGOX9F3bacp9tuyh+4AvACCWZfOykOJY1cSRTKuVTAMwBAAAwk4wJUwClkkwcycpZLgoAAPPEWVlTr8i4iN4bAACzznsZF8nUK4qUSryMGxjrHci9tuv14LoAY7vnUilSOd6a/+feA6j/k84AAEYnXwegciy7GQAAAMDs2wwAzP0DADBfvJcVg38AAOasCyBZlWIpJQUAADAX0kwqxYpMpSSfJLwHABgbVrvtfU24LsDI77YkkamUFClJ2AEAjLvWYffaz7UBxiNJZDn+FwCAOWOMOAcYAIA5RAAAAIAAAAAACAAAAGAmRZwECIyZ96x2335B8mvieSspQAcAAAAQAAAAAAEAAAAQAAAAwDWKWIwEjBlH3u5+PbguAB0AAAAw6g4AkRugBVCoNgAAOgAAAIAAAAAACAAAAOA6sQsAGDemuve+JlwXgA4AAAAYZQeAyA3QAijM8J/rAtABAAAABAAAAEAAAAAAN4BdAMC4cc/tvB4sAQDoAAAAAAIAAAAYAbYBAmNHr3vva8J1AegAAACAEXYACNwADQAaAAAdAAAAQAAAAAAEAAAAQAAAAABTiW2AwNhxz+28HqwCBMYfALjfgMnUO1D/gQliCgAAAAIAAAAgAAAAAAIAAACYDewCAMaO1W47ZVwXYOwBgPsNoP4X5ZpwXYCxYQoAAAACAAAAIAAAAAACAAAAIAAAwHCw+A8Yu0ieOw8Yb7FjufuO6zH4BYAOAAAAIAAAAAACAAAAIAAAAICrxlHAwLixBnDva8J1AcYYALjjABIACQCYO0wBAABAAAAAAAQAAABAAAAwBExzAygAdgEAkwgA3Hc7rwfXBaADAAAARt0BIHIDExjyghYAQAcAAAAQAAAAAAEAAAAMHbsAgHFjqnvva8J1AegAAAAAAgAAABgitgECY0eve+9rwnUB6AAAAIARdgAI3MD4B7vYffDPtQHoAAAAAAIAAAAgAAAAgBvBLgBg7Jjs3v16cF0AOgAAAGDEHQACN0ADoEhNAAB0AAAAAAEAAAAQAAAAAAEAAABcNbYBAmPHPbf7NWEVIDDeAMD9Bkym1mFn7ee6AGPDFAAAAAQAAABAAAAAAAQAAAAwG1gECIwbi932viZcF2CMAYA7DiABFKb6c12AcWEKAAAAAgAAACAAAAAAAgAAAJgNkWfNDTA2XpL3LHXbdk3ytX8+/wIwpgDAowiYSAzgMux6TbguwLgwBQBMpNgBAAEAAAAQAAAAAAEAAAAMHe8CAMaNtW57XxOuC0AHAAAAjLIDQOQGaAEUZvjPdQHoAAAAAAIAAAAYHhYBAuNGp3v368F1AegAAAAAAgAAACAAAACAG8U2QGDsuOd2vyYsAgDoAAAAgBF3AAjcwGQGu6ABANABAAAABAAAAEAAAAAAw8UuAGDsmOze/XpwXQA6AAAAYMQdAAI3dkuGxnARRpK4Tfji+m4y3mxdF3FdRiHzPOixWwAALin8vTTVhaQnZ6w8CXGonDHqdjtq9npcjFw3TZR1O7oQxUqylAsyzHAlo9RnqkWxYucIAiAAYK+HhdRNU/2jH36P3nr4Zm30uooMs0RDvcZGSrJM+6q1/M/nd8Tr8v/2n7j7Pr396HHFzslToIYq8ZnqcUl/8sKz+l///b9RbC2RHgQA7BUBpI985ct68OTtesuho1ySsVzxeQ1D4b/++OKyji8u82EYke+fPaOPfOXLA584IgDyT8PKL/0CnwZsssboYrej5UpV//pv/x3df9MRddJUkbX5g4M52hvnZVgHsHU1vFfGZ2tony3JKMkylZ3TV156Xn/1dz6lc+2WGqUyUwC4JAD84v/JJwLbRNZqo9fVUrmiz//YT+neg4eUZFkeAgAUWf9effTlF/Xgb39S65226nFJSZZxcbB9wMclwG4PkHpU0nq7rQd/+5N69OUXFVnLAwSYtuLfbqseUfxBAMC1PEh8pnqJEABMdfEvlZR47lkQAHA9nYCYEABMZfGn7Q8CAIbSCegQAoCpKP4dRv4gAGCYnQDWBADTMfJnzh9XyVXf+64PcxlwJZnPVIkiXeh29bnvfEMPnDilwwuLSrKM7WzApIv/v+iv9o/z4s89iasJAP/ZOwkAuMoQ4FVxkS50O/rcdwkBQDGK/ye25vxp++MaMAWAa3vwDO4O+BefYDoAKELxZ84fBACM6wG0uTuAEABMfuTPvQcCAMbeCegQAoCJFH9W+4MAgIl2AiI6AcBERv6s9scNYhcAbkjmfdgd0OmE3QG3sDAQGFnx/8zAnD/FHzceANgFgCGFgG5Xn/vOY4QAYOjF/4VQ/Dsd1UsxxR9DwRQAhvagqsdxWBPwmU/o0ZdfYDoAGGrxH9znDxAAULgQkK8JIAQAwyv+rPYHAQBTEQL6uwMIAcBwRv7M+YMAgKl4gKUDuwMeIQQA11X8HxlY7Z9y72D4XPU9LALE8O3YHcCxwcDVFf+XXmC1P+gAYNofaOnWscGf+YQefYlOAHBtxT/l4mCEHYD3/jAdAIypE/AYnQDgssX/1xn5gw4AZusBt9UJ+HU6AQDFHwQAzF0I6BACgF2LP6v9MWYsAsTYZN6r4iJd6DIdAIr/jpE/+/xBBwAz3wmIS1pvtfXgI3QCMOfF/5Ff13qL4g8CAOYpBPTXBBACMM/Fnzl/EAAwfw/ClBAAij9b/TBBrvoetgFiMnZsEbzlNGsCMAfF/+OM/EEHANg+HfBxOgGg+ANj6wD8CB0AFKgT8G06AZjR4v9bFH/QAQAu3wn4LToBoPgDBAAQAnhgguIPEABACAAo/sCNc9X3vOPDXAYUyeaagG5Hn/v211kTgCkt/g9xvC/oAADX/CBNM9WjfifgIToBmL7i326rHpWUpHxmUdQOALsAUPROQKejz32HTgCmqPi3GPmDDgBwww/Weil/dwCdAFD8AQIACAE8YEHxBwgAIAQAFH+AAIDZfOCmhAAUvPjzYh9MD1d9gG2AmB47jg0+ycJATLD4/+ZDWm+3VI8Z+YMOADCWB3A9Lmm91dKDv0knABMs/i2KPwgAwPhDQIkQgAkXf+b8QQAAJhwCHiYEYEzF/2GKPwgAQHFCQJtOAMY08m9T/EEAAOgEgJE/MKXYBYCZkPlMlSgOuwO+9XU9cOtpHV5YYncAhlD8n9eDn35o4K1+qSQ+U5iJAPCXCQCYkRAwsEXwW48RAjDE4s/IH7OHKQDM3IN7c03Apx/Soy89z3QAKP4AAQCEAIDiDxAAMB8h4GFCAK6x+D9M8QcBAJjeB3o6cGLgpx/Soy8SAnCF4v9iPvLvn/CX8lnB7HLVd7MLALNrx8LAk6d1eJGFgbhM8Wfkj7kJAOwCwNyEgHbYIkgIwK7F/2MUf8wVpgAwNw/6rYWBH2M6ABR/EAC4BCAE8MCn+FP8QQAACAGg+ANzwFXfzRoAzJdtCwO/mR8bzJqAuZAOFv9PUfxBBwCY707Ap+gEzEvxd9bq26++TPEHCACY7xCQEgLmROa9nLVab7f0gX/5iF5rXhx4sQ8wn1z13T/0YS4D5rkwbG4R/ObX9cCttzEdMIM/Y2uM1tttve/hh/Snzz2jlWqN4g86AFwC0AkYnA74KJ2AmS3+H9MXn3lSq9U6xR8gAACEgHkq/mu1hnoUf0CS5KrvYhcA0C8Y23cHMB0wE8X/03nxrzfUSyn+AB0A4EqdgN+gEzDdxb9F8QcIAAAhYP5G/g9R/IHLYBcAsEch2dod8DWmA6Zu5N8v/nWKP0AHALiRTsCv0QmYluL/8EP64jPfp/gDBACAEDBXI/+nvx9W+1P8gcty1XcxBQBcqcBs7g74xtf0wCmmAwpd/JnzB+gAACPpBHySTkAxin9G8QcIAAAhYP6Kv6X4AwQAYHwhoEYIoPgDM8BV3/X2D3MZgKvnB7cIsiZggsX/YxR/4EYCQOWdLAIErr0QeZWjSBe6LAycRPH/65+i+AM3iikA4DolWaZaXNK5VksPfuLX9OiLzzEdQPEHCADAPEjzNQHn2oQAij9AAAAIAYQAij9AAAAIAaD4AwQAYGZDQEoIoPgDU8NV3sk2QGBYfH93QKetzz3G7oAbLv6/8VGKP0AHAJiWTsDgdMCv6tEX6ARQ/AECAMbAGCNnrJy1ii75ctYyEiUEUPyxvRDkz4xot2eG4Zkxs7Vi+cM/57kMs1L0jTLv1U1TtZOe0iyTz7xkJHlJRnLWKnZOlSiWs1byUuopSqPirFWz29VyparPf+C/1b2HjyrJMkWW7E3xn3zRt8Yo9V6dpKdOkirN0s1nxdYzw6kcOZWjePMZk3nKBgEAhbiJjTFq9XpqdrsqRU5HF5d1am2/Tu3br5uXluXzX9dLU33l+Wf1/Pl1Pf7aK1pvt+SsVaNUVmRtCAxcUkIAxX/mP4/ee210u+okiWqlWCdX13RseVX33HRYy9Xq5muWz7Va+vpLL+gvzp3Rk2deU7PbUzmKVC+VZIxRSkeLAIAJ/ODyG/lCp6NemurU2n792N336UdO3657bjqslWptz382zTJ977VX9GfP/YV+57FH9e+ffEIXum0tVapyxtIRGDYvOUcIuKri/9T3tdag+I9ssCCjc+2WImt13+Gjev+b7tfbT9yiNxw4pGoc7/nPtno9ffOVF/Wlp5/SZ772FX31heeUZJmWK1V50RGY3gDwv/9DfnLTluCNVeIzrbdaevPRY/of3v5O/Y033KOFcnngwerlvd8xou+3/QZ946UX9St//AX91qNfUbPX1VKlqiTjATzSTsDf/e/mPgRsK/6f/DV98ekntVavU/xHILJWzV5P3TTVj952p/7HH3qX3nnyVJgGHHhm7FbIL31mpFmmP3ryCf3jL/47/d73vq2Sc6rFMetbCAAYx418odNROYr0v7zzPfoH73hAsXOSwtn0xkhWYVrgMgPSEA7yNl//1/7Zc3+h//n/+5f6wlPf12qttvnrQAgYffGn7T/KZ8aZZlMnVlb1fz34N/S37r5v8+/1t6caY3S5ZX79Z0Hm/bbP6mcf+6p+/vO/q6fPntFqrUYImLbnEecATNeNvN5u6fb9B/U7P/539f5775e1VlmWbS4CHCzoe6Y+hUWD/V+bea/Uex1dWtbfuf8t6iSJvvDkE4qdkzWGdQFDtHVOQEefe+xRPXDq9rk7J4DiP97AeabZ1PvufKP+n5/8+3rLzcfDSF9+c2R/peJ/6TND2lo4/IaDh/T+e+/X9159RY+++LzqpRKDBgIAhl/8nV5vbujtx0/qD376gzq1tn+zaNiruIEve3Pnv0fmvayM3nP6Dh1bXtFnH3tUsXNXDBQgBFD8izlgeP3iBf3M29+p3/gvf0KNUllJlg1lK3A/OCRZpsVyRe+/93693tzQHz3xXTXKFdYEEAAw3JF/W3/p+En9q5/6b7RcrSrN28bDLBfGGMkYJWmqHzhys06s7tPnHntU5SiiC0AIoPhP2TPjtY0NfeiH3ql/+r6/tbla3w15qskObAv8q3e8QWdbLf277z+uRrlMCCAA4MaqRLhhN7odnV47oD/8ex/UUqWqzGdDv5E3Q0D+kOjlIWCt3tBnv/7VcEP3zxTAcH68mVc5no8QsKP4PzVQ/PlMDfWZETmn1y5u6Cfu/0F99G/+V0oHOoUjeWbkHcjMez14xxv05JnX9OVnng6dAJ4ZBQ8AP0wAKCojk6doo9/9qb+vW1b3Kc1GV/y3JXtrlWSp3nrzCT115vX8hibVj7wTcHr2QsC24v8JRv4jvW+N0UanozsPHNRv/8R/rZKLts3dj+xZlf/+XtK7T92uz3/7G3rp/HmVokg8MgodAP4SAaCgImt0ptnUL/zoX9Pfvvu+sa4WN3kAkZHecfJWffbrj4aDg5zNb3MMPwS0Zy4E7Cz+T1D8R3zjJmmmR378A7p9/0Fl3o9lwNAPAZn3qsUlveGmQ3r4z/+TIp4XxQ6MXILiJvkLnY7uP3pMP/uX36XUe7kxFwNrjNLMa1+toX/0V/66LnY7nAk+ItveHfDxX5mJdwdQ/Mc9YLA622zqAz/4Nr3jllObC/7GOqLMP7PvuOWUPvCDb9PZZpMTLwkAuJ403UtT/YMffkCRdfLeT2Q1fv/s7//ijW/SW46d0EaHEEAIoPgX8nmRZVqt1fSz73h3OONjgkXFe6+ffce7tVqrqZdvUwYBAFd5M7d6PZ1a26/33XW3fF6IJ/W9ZN6r5CL95P1vUbvXIwAQAq6t+D9F8R/5g9wYXWi39d7b7tSpffuVKazjmcj3Yq0ySaf27dd7b7tTF9ptnhmFDQBe4qtYX05GzU5X73/T/arFJaUTTtAuPwzob959r44sraiTJGF9AD+rkXylaaZaXNK5VksPPjRdIWBb8f/1geKfpPxsR/jlMy8now+8+W1hxn3SK+/yY8g/8Oa3ycmEt5LycyrcFx2AQj5EvUrO6b233RFG4QXoSHjvdbCxqPuPHlOzSxdgrJ2AKQkBO4o/bf+x3Z/dNNWRpRW99dgJmbwjMOmOhJH01mMndGRpRd00ZRqgkB0AFPBmTnRkaVl3HTy0eTMVIZRI0ttP3KIsS8UBweMIAWkeApp68KFfLnQI2F78fzUv/nX10oQf5MiLrdTqdfWmw0e0WAmn8E262PanDhcrFb3p8BG1el1Z6n8RAwB9kCJ9WSO1e12dXtuvlWqtEDfzYBfi/iM3y1qTn/fN12i/BkNAq7AhYO/i3x/587Mc5ZeRlKaJ7jtydODskGJ0Mo2M7jtyVGmayPBZKF69IQMVrAOg0P49vbY/3C4FuZn7IeTYyqqWKjUlBQkm89EJyAobArYv+PtV2v4T4CVZ63Rb/swomtvW9staR8+wmB0AFO6G9l63rO7bvLmLEkwk6dDikpYqlc2zxTGmEJAWLwTsGPk/SfGf1PPCWasT+TPDFOyZcWJ1n5y1vCWwgHjLSxHjvFdhH6JJloXzvbd3qjGuELC5O+CX9fmf/u917+GjYz0h8rLFv5Gv9sdEnhtFvfY7doCADgCukJ4L2l6n6T/hENCfDmhNrhOwZ/Fn5M8zY4q+LxAAAEIAxR+YSxE9maKZhj7ZztXqGHcISFUrxTrXaurBhz6iz//0B0c+HbC9+P/zgeLPVr9i3JPT8swAHQAAQ+wEfGSknYAdxf8pRv4AAQDA5EJAOhACPvYRPfr88EPAriP/Ggv+gGnHLoCiKXqnzE/J9zlvISDe6gR8/qc/qHuPDGc6YFvx//g/3znnz8+/WPclzzXQAQDmLARcOh0whE7AjuJP2x8gAACY7RCwa/HnkB+AAABgdkMAxR+YD2wDLJxpmCxjG2CxQ8DgFsF/lq8JuPmq1gRsL/6/MlD82epX/OcGzzVcawDg51HM+2Ra7mU+P8UMAf2Fgc2wO+Dzf+/KIWBb8X9ooPiz2p/6T/2fSUwBADPbCbh0i+Cze04H7Cj+vNgHIAAAmO0QsGvxZ7U/QAAAMLshIPOe4g8QAADMTwgIuwOsMdvn/Cn+AAEAwIyFgDTNQ0BTf+Wj/1SPvfi82r2e/trHfllffPJxVvsDcyaSZ1lmoXiff03D95h/YbpCQFzSmY0N/dgnP6qDC4v60lNPaF+trl7SL/78TKfyuVHk88N5XhQ0AACYrxCQZWqUy3pu/ayeOvO6Vmv1kbxBEAABAEDBZN4rdpFKLgQCAAQAAHPCe0+zH5hjLAIEAIAAAAAA5gG7AIpmc6WsL/73yKpeoED3ZFG/N/G8oAMAAAAIAAAAgAAAAAAIAAAAYIQiNgIXjB/4mobvkc8PUIx7kuca6AAAAAmA7w9X7gDwgyloVJ6WOM/nB6DA0gKgAwAAAAgAAACAAAAAAAqCXQBFwxIAANdzT07LMwN0AAAAAAEAAACMEdsAC4eTgABczz3Jcw10AAAAAAEAAABcil0ARTMFnTLjjfr/B2DC9+M03IvMABQ0AADXqJel6qVJuK89dzQwSdYY9dKEexEEAIx4tGGM9tXqMjKKnOWhAxQgAJSjSLFzXAxcawDgAV48fo//PdnCL0kL5bK+8D/9vLz3TAEAhXlieDXKFUmSs7Zw3x1zAHQAMCMdgKVKlQsBAFMfAAhkxQzLRf4WafsDhQznhX6m0QCgAwAeNACAyeMcAAAACAAAAIAAAAAAZhLbAAuH1TIAeK5hHAGAn0fx7pOM+wQA9R+jxRQAAAAEAAAAQAAAAAAEAAAAQAAAAABTKhLnuheL9+JnAmAmn2s82+gAAAAAAgAAACAAAAAAAgAAABi6SMZKWcaVKIr8uEzPYhkAs/BI856jgAs5/LeKlKUy1sqnmWS4JkVJALF1XAoAUy92kUgAxSoxxln5LM2nAKzlB1Ogn44xRk+9/qokyZDKAEzlkyzUlKdff1VpmskYnmWFSQA2L/3Ul+KN/52zevyVl0MA4KYBMMW+9/JLyrKMwUzRGMky1VwsmfeqxLEef/VlnW1uyBrDegAAU8caK++9vvrsM3LObXYEUJDBppessUZilFmgH4pXyUV6/uwZfevFFzZDAQBM03PMGqPz7Za+9tyzqpZinmOFGv0bGWsUyZvQC5BhGUCBknO3l+oPvvUNvf3W0/xYAEyVLA8Af/LUk3r+7FktVCrKMp5kBan+eb03rAEootRnqpVL+syf/Yma3Y6ctUwDAJiqGmMkfeI//gelGQsAi/ozst6E7QDymbZv1eBrUl/eZ6rGsZ54+SX9v1/7qoyklAAAYBpG/1kmK6MnXnlZf/Ctx7RQKSvzKc/2onz5TD5L5Y3lJMCi8l6KI6f/+9/8npI0lTEcDgRgCgJAvpX5F//t7+vMxkXFjpfOFlWYAkjTzX2BKMhN5DMtlKv682ee0i/+29+XM1ap58RGAMWVZpki6/SFx7+rT3zpC1qpN5RkKRemUFXfhppvJJsPLdkJUEBJlmmxWtMv/Ovf1VeeeVqRdUo5thlAIQctYeR/rtXUBx/5lCQOMiukgZofyXt5GRnn5JOUIFAoXs5atTs9/eQnPqov/vz/puVqLcyx0bEBUJQnlffyPjyvfuaRh/XYc89q38KikpTRf8F+UDKxk8+85H3eARj8QqGkWaZGuaLvvvyS3veRf6xzraastXQCABRm5J/lxf9Dn3lYD3/5S9rXoPgXdvQ/8JUfCGzCvAArNQopyRIt1ar6D098V+/7yC/pXLMpZ62SNOWMAAATfDZlssbkxf/T+md/+Hva1+jP+/N0KmIHQNaGmi/JVd7+1g9772WskZKENwIWOGU3KhU98cpL+ldf/6p+4PgJHVvdJyOvNAtzb/zoAIxDmmVSPup/+fy6fvzjv6pPf/k/anVhge5ksROATBSmAIy1IQBYY6Qsk9KMaYCCh4BaqaSXzp/Tb/3pl+Ulve3kKUXOyeRp3Hsfznni5whgaGXDy/twHok1RtYYGWP02T//T3r/R39Zf/r0k9pXr1P8p4CxRsbacBzw0s99yMtLvtMJf5GtZkW/E0P7P8t0vtXUm4/fog898F795/fer4VKZVtY8N7zAg4AN1IuNgv+4Oj/j777bf3SH/6+fu9bj6nknGqlkpI06/8jKG71l88ymXI5nNa49HMfChWi3Q2tgR7TANNxW4YgcKHTVi9JdergQf3Y/W/Re+56o+45crNW6nUuEoChaHW7+uaLz+tLTzyuR/70j/XVv3hGSZZpuVqTl+dFP1MyeDRxFHb7VUqhjiz9w5/xMka+25ONY/lOhws1RfqtuFavq1anqzhyOrqyqtMHDurUgYO6eWWffH4yFwBcVa3Ij4ZZbzb19eee1TNnXteTr76iVrerUhypXirLGEPLf9oGjuWysl5PphSHLYGbHYA0lbFOvt1mHcA0/mCNkTNGmffqJonaSS/cnLyBC8B1P1gka53KUaRyHG8+YxjxT2eqM5VKePePc5KkaKCCSGkitm5M68/WK8l/dHHkVI6j/P4lzAG47idLeIWM98p8tvmMwXT+LJUm2479j2Tymm+MfJonA9o6Ux8GeHsgAGCTc1s1PowOFW0O+I2Rl5F1kXza5WIBADAjjIuUJcnWejCv7a8DNpwICADAbMlPADR2+5SwvbRFIM5vBgBgtgy2/3cNANbIJ8mOXwQAAKaUc6G2X7YDIBP2jEcEAAAAZoGJXL6fY3sAiHb8QheFLYGe9wIAADDV8lOdjNtR7rXLX7FSN5E8xR8AgOkOAEbqJVJpZ7m3O3sFNuwVjJkGAABgqsX5/n9jryIASPJxJBPFbAcEAGBqR/9eJorl42jXv71rADDOSb0eFw8AgGnW64WafrUBYGsaIOLiAQAwjeJoz/a/tNsiQEkykneRrHPyvV74C6wJBACg+Hz4f8aVlXmz54a+PYf4JsqnAfpbB1kOAADA9ISAXk8mjvf8JXbvv2PkPdMAAABMnTgKNdya6wgAxkiO3QAAAEzX6D+s/lf/YL9rCgD9gh/F8t3uZRMEAAAoEGtC7Y7i7TX9qgJAPzFYI28kUypxQQEAmAKmVAqH+fYH73t0Aa44wW/iWEq9lHneDQAAQJF5L2W67OK/qwsAxkjOynda4WjgXsp2QAAACln8FY7+TbpSqZq/2G/vNXz2KpoJUhTnJwmxGBAAgKImAONcPvd/5dG6vWIrQXmi6PYka7m+AAAUkbWhVvdf5neFHXxXV9GNlZyTKbMlEACA4g3+fajRzu159O/1BQBJKsXy3YSFgAAAFI0xoUaX4qv+R6Kr/Y1lrWRM2F7Q7hAEAAAoyui/Ug4v/slr9dV06+3V/ub9LoCyjIsNAECRZNnW6P8qp+qv7aB/55R1OrLlOCw0AAAAE2XKsbI0lXHla/rn7LX/i8rsBgQAoCh8Xpuv0bXv63NOPstkrmGhAQAAGMHovxTLZ1lY/T/yACBJlbJ8km5LHwAAYDwj/s3/maRSpXxdv010zf/E5o4AK1OO5VvtfMUhPxMAAMYi8zLVSggA17Dy/8Y6AD5/KVClJN9LOB0QAIBxszbU4Erpuor/9XUA+iHAWslZmVIk32xxLgAAAOPgvUy1Kp9moRZf5wm9NzZ8r5Tluz2ZOOKIYAAAxlH84yhsxb/Ouf8b6wAMKpWklCOCAQAYuf46PHfj5fvGf4c4ku92ZWIOBwIAYKT1vxSH1n/lxsv3cFbwVSvhDGK6AAAAjGz079NUqlaG8tsNJwDY/uuCS6wFAABg2LwPNda5oe2+G94evmpFWa/HCYEAAAx78F+KlfV6Qxv9S8NYAzD4DVYr8u2WZEQnAACAoRRXI+9TmWp1qL/tcE/xcU6yTAUAADAU/da/ddd13v/YOgCSpGpV2YUN2VKJXQEAANzI4L9UUtZNZBbqQ/+9R3KOr6lVwtuJOCYYAIDrrNA2vH23VhnNbz+S39U5KY7DCYEAAODaB9NxJMXx0Fv/ow0AklQuKcu8TKXMegAAAK6WD7Uzy7xULo3sXzPSHr2pVeSTJN8a2A8BhAEAAC6p+pt/NKU41M4Rtf77RtujN0aqVuVbzfC/s2zrtYUcGggAQKj9RpIPa+e8z6RqbeSn645+kt5ZKYpllMpnPRoBAADs2gAwMi6Sj1yonSM2nmX6lbIyY1gPAADArgEgn/c35oZf81usACDJ1KthUWCtSggAAGCw+NfyGlmvju1fO9aN+mFRYCoTx4QAAAC8l4njUBtHvOhvogFAxki1cjjYgBAAAKD4h4PzauWRL/q71PhP6rFOqtekViucFJimY/+PBgBg0sU/HPDjQ020buzfwmTO6o2cfDlPO9bSCQAAzFfxtza85a9cliI3kW9jcmf1lmIpy2SSRL6XbJ0RAADAjBd/E0fyURRq4YRM9m09lbJ8FIXzjukEAADmqfiPabtfMQMAIQAAQPGf0wBACAAAUPzHrjjv662U5duSySTvWRMAAJih4u+KVfyLFQD6IUCSkZdPPCEAADD9xT9y8qViFf/iBYCBELBZ/DknAAAwjcXfubDVrxQXrvhLRVkDsP2qhQvVqErGcGIgAGDqir+J4zB4bVTz4l+8OlbAAJCP9qNYqtfkjQgBAICpKv7eKJzwF8XbaxsB4CrFkVSvhxBQ5S2CAICCF/9qNS/+9VDDCiwq/AV1Vmo05FstmXJFvt3OwxTrAgAAxSj8kmQqFXl5qdGQbPFrlJ2Ki2tN/rIEyZTicFbA5nyKVxHnVgAAM1vxt9cga/PapLxWTccANZqqS16vSe2OpI6Mt/K93kAngBAAABhnUco25/t9uVTIlf7T3wEYVCnnCcvKVCrUfgDA+BsACi1/WRtq0pQV/+kMAN5LUSS/kIeAUmlq2i0AgBlgTV57bKhFUTSVi9SjqfuO+y1/Y+XrVanTDa8VlpHvdlkcCAAY2QDUlEry8qHlXy7trE10AMaoXJIaNXlrtqYEAAAY9vizUpG3RmrUthf/KWVn4qcSRdJCXd7ZzTcucWYAAGAoo/78TbXeWWmhHmrOLJTO2YlmRqpXpVIczgyolOU7HT68AIAbGPWXw97+eqPwB/vMZwdgIKkpjqSFxkA3ID9GmI4AAOBq6kj/LP/NUX9e/GesjsxWnNlcIGg2t2X4jWZYrZll8knCIkEAwJ7F30R5u98aaWExvNHv0hpDAJgCzkmLC/KdrtRshwUc+a4BggAAYHPUb21o96eZVK3MxCK/K7Fz8cMtl6TlBfnIScayWwAAsDWwr1TC1vLIScsLc1H8Z78DsO0nbKRaVSqVpHY7pD3nODsAAOZ01G9KJfk0lZyR6gtS5ObqEkTz9gNX5OQbdalaCesD4lgyku/28tc1EwYAYEaLgOTzl8p5yTsjLS7K9+f5vZ+rAeF8BYDBH2x/fUCvJzVbW/s8u13uEQCYxRLQXxBu845wHO9dIwgAcxAI4lhaikMQ6HSlnglvd+r1pCx/5SNTBAAwZYN9L8mEc/vzZ7qPnFQeKPzGzPUW8YgPyEAQiGOpUpGarbBYsOTC/BDbBwFgup7tURTWeaVpeGPfyvLWHH+/8M/5+TARnxRt/xBETn6xEdpErY7UCQtFJIWugBddAQAo4mjfKKzrCk9p+VIsVRvy1u79zCcAYAdrw9HCtYp8L5FaHSlNw1qBJA2HCskQBgBgkkVf+eE9kZPvJWFBX7UcTu7j2UwAuC55bZcxUikOX0kaDhLyXpKTiSL5JJWStP+J5AMHACMv+got/sjJJ0k4rjeOpUZ951a+/rMcBICr/6DtdrWcFFVDZ6CXKOt2ZbJMimweBhIpTfdIEwCAG3oIR27rWWuNsjiSqdcu/5Ieij8BYOjiKEwH1Gt5GOjIZKnknUzkJO/DhzTL+q2EPToMXEoAc1zjdx2h53/BhsGVjAndVmuUxW6z6PP4JABM8MObt/w3w0Bd6iVhu0m3K2OMTBSHMweyLKxGTdOtqu9JAADm/Bkqs7W42jkZ58IarDSV95kya8JC7Ea8s+h7pl0JAJMy+MEbCAOKI5laVfJZWEDY6cn3Mhnlr5h0NnQI0ix0CPovJ+LDDGAeBk35y3dCwbfhr6WZfJYqMwpd1HolPEuN3f33uPQZDAJAocKAseHdA6VSSK39aYFuIp/0ZLwP90EcSdZJ1oQw0A8H3udTCLv8/rv9ewFg7CN47f2MsqG4bxZ5a8MBa1mqLMtkrFHmbOiUlqKwsG+3ZxpFnwAwlWEgfHoVNqhuHThkVA2/1PtwvkDqJZ9JnY7kTfj1/XdT92+k/o0wcNP5wZAAAONi7fZibczWsy9/Jvk0dDd9vkdfpTgMjJzJ38Ni9pgEvWR6lKJPAJjiRLD3h9uY0CXoq1W3Cn2ahQKfpvJJKuOzsOXQK4QFKQSHOT/OEsBkBjne9p9jNjzSIidvbGjhOxd+XX/0f/lWwiXPSQr+OPz/XhRXq1s8U9oAAAAASUVORK5CYII="


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def make_web_handler(app):
    """Tạo lớp xử lý HTTP gắn với App. Mọi thao tác dữ liệu chạy trong luồng giao diện qua app.call_ui."""

    class Handler(BaseHTTPRequestHandler):
        server_version = "FshareAutoDownload"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            if "Cache-Control" not in (extra or {}):
                self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin", "")
            if origin.startswith(("moz-extension://", "chrome-extension://")):
                self.send_header("Access-Control-Allow-Origin", origin)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200, extra=None):
            self._send(code, json.dumps(obj, ensure_ascii=False), extra=extra)

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            if not n:
                return {}
            try:
                return json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            except ValueError:
                return {}

        def _authed(self):
            pin = str(app.cfg.get("web_pin", "") or "")
            if not pin:
                return True
            if self.headers.get("X-Pin") is not None:          # tiện ích trình duyệt gửi PIN qua header
                return self.headers.get("X-Pin") == pin
            cookie = self.headers.get("Cookie", "")
            m = re.search(r"(?:^|;\s*)fpin=([^;]+)", cookie)
            return bool(m and urllib.parse.unquote(m.group(1)) == pin)

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path in ("/", "/index.html"):
                html = WEB_HTML.replace("{{TITLE}}", f"{APP_TITLE} {APP_VERSION}")
                return self._send(200, html, "text/html; charset=utf-8")
            if path in ("/icon.png", "/icon-180.png", "/icon-512.png", "/apple-touch-icon.png",
                        "/apple-touch-icon-precomposed.png"):
                import base64
                data = ICON512 if path == "/icon-512.png" else ICON180
                return self._send(200, base64.b64decode(data), "image/png", {"Cache-Control": "max-age=86400"})
            if path == "/manifest.json":
                return self._json({"name": APP_TITLE, "short_name": "Fshare DL", "start_url": "/", "scope": "/",
                                   "display": "standalone", "orientation": "portrait",
                                   "background_color": "#0f766e", "theme_color": "#0f766e",
                                   "icons": [{"src": "/icon-180.png", "sizes": "180x180", "type": "image/png"},
                                             {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png",
                                              "purpose": "any maskable"}]})
            self._api("GET", path, {})

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin") or "*")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Pin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            body = self._body()
            if path == "/api/login":
                pin = str(app.cfg.get("web_pin", "") or "")
                if not pin or str(body.get("pin", "")) == pin:
                    return self._json({"ok": True}, extra={
                        "Set-Cookie": f"fpin={urllib.parse.quote(pin)}; Path=/; Max-Age=31536000; SameSite=Lax"})
                return self._json({"error": "Sai mã PIN"}, 401)
            self._api("POST", path, body)

        def _api(self, method, path, b):
            if not path.startswith("/api/"):
                return self._json({"error": "Không có trang này"}, 404)
            if not self._authed():
                return self._json({"error": "Cần mã PIN"}, 401)
            routes = [
                ("GET", r"/api/state$", lambda m: app.call_ui(app.api_state)),
                ("GET", r"/api/folders$", lambda m: app.call_ui(app.api_folders)),
                ("POST", r"/api/folders/add$", lambda m: app.call_ui(app.api_folder_add, b)),
                ("POST", r"/api/folders/(\w+)/update$", lambda m: app.call_ui(app.api_folder_update, m.group(1), b)),
                ("POST", r"/api/folders/(\w+)/scan$", lambda m: app.call_ui(app.api_scan_files, m.group(1))),
                ("GET", r"/api/folders/(\w+)/files$", lambda m: app.call_ui(app.api_files, m.group(1))),
                ("POST", r"/api/folders/(\w+)/save$", lambda m: app.call_ui(
                    app.api_files_save, m.group(1), list(b.get("add", [])), list(b.get("remove", [])))),
                ("POST", r"/api/scan$", lambda m: app.call_ui(app.api_scan, b.get("download", False))),
                ("POST", r"/api/stop$", lambda m: app.call_ui(app.api_stop)),
                ("GET", r"/api/rows$", lambda m: app.call_ui(app.api_rows)),
                ("POST", r"/api/download$", lambda m: app.call_ui(app.api_download, list(b.get("codes", [])))),
                ("POST", r"/api/mark$", lambda m: app.call_ui(app.api_mark, list(b.get("codes", [])))),
                ("GET", r"/api/history$", lambda m: app.call_ui(app.api_history)),
                ("POST", r"/api/history/upload$", lambda m: app.call_ui(app.api_history_upload,
                                                                         list(b.get("codes", [])))),
                ("POST", r"/api/auto$", lambda m: app.call_ui(app.api_auto, b.get("on"), b.get("interval"))),
            ]
            for meth, pat, fn in routes:
                m = re.match(pat, path)
                if m and meth == method:
                    try:
                        return self._json(fn(m))
                    except ValueError as e:
                        return self._json({"error": str(e)}, 400)
                    except Exception as e:
                        return self._json({"error": f"Lỗi: {e}"}, 500)
            return self._json({"error": "Không có lệnh này"}, 404)

    return Handler


WEB_HTML = r"""<!doctype html>
<html lang="vi"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,viewport-fit=cover">
<title>{{TITLE}}</title>
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Fshare DL">
<meta name="format-detection" content="telephone=no">
<link rel="apple-touch-icon" href="/icon-180.png"><link rel="icon" href="/icon-180.png"><link rel="manifest" href="/manifest.json">
<meta name="theme-color" content="#0f766e">
<style>
:root{--bg:#f2f3f7;--card:#fff;--text:#111827;--muted:#6b7280;--border:#e5e7eb;--accent:#0e9384;--accent2:#0b7a6e;
--green:#059669;--orange:#d97706;--red:#dc2626;--soft:#e9f6f3;--soft2:#d8efea;--btn:#e8ecf4;--log:#0f172a;
--sat:env(safe-area-inset-top);--sab:env(safe-area-inset-bottom);--tab:58px}
@media (prefers-color-scheme:dark){:root{--bg:#000;--card:#1c1c1e;--text:#f3f4f6;--muted:#9ca3af;--border:#2c2c2e;
--accent:#2dd4bf;--accent2:#14b8a6;--green:#10b981;--orange:#f59e0b;--red:#f87171;--soft:#0f2a27;--soft2:#143633;--btn:#2c2c2e;--log:#0b0b0d}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--bg);color:var(--text);font:16px/1.45 -apple-system,BlinkMacSystemFont,"SF Pro Text","Segoe UI",Roboto,Arial,sans-serif;
-webkit-text-size-adjust:100%;overscroll-behavior-y:contain}
header{background:linear-gradient(120deg,#0e9384,#115e59);color:#fff;padding:calc(10px + var(--sat)) 16px 12px;display:flex;align-items:center;
gap:12px;position:sticky;top:0;z-index:20;box-shadow:0 1px 0 rgba(0,0,0,.15)}
header img{width:36px;height:36px;border-radius:9px}header .t{font-weight:700;font-size:17px;line-height:1.2}
header .s{font-size:12px;color:#ccf3ec}header .r{margin-left:auto;text-align:right;font-size:12px;color:#e0f7f3;line-height:1.35;max-width:52%}
#ptr{height:0;overflow:hidden;text-align:center;color:var(--muted);font-size:13px;transition:height .2s}
main{max-width:900px;margin:0 auto;padding:12px 12px calc(var(--tab) + var(--sab) + 90px)}
h2{font-size:28px;margin:6px 4px 10px;letter-spacing:-.4px}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}@media(max-width:700px){.cards{grid-template-columns:repeat(2,1fr)}}
.stat{background:var(--card);border-radius:14px;padding:12px 14px;position:relative;overflow:hidden}
.stat:before{content:"";position:absolute;left:0;top:0;bottom:0;width:4px;background:var(--c)}
.stat b{display:block;font-size:24px;color:var(--c);font-weight:700}.stat span{font-size:12.5px;color:var(--muted)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.btn{border:0;border-radius:12px;padding:11px 15px;font:inherit;font-size:15px;font-weight:600;background:var(--btn);color:var(--text);cursor:pointer}
.btn:active{opacity:.7}.btn.p{background:var(--accent);color:#fff}.btn.g{background:var(--green);color:#fff}
.btn.d{background:rgba(220,38,38,.12);color:var(--red)}.btn.sm{padding:8px 12px;font-size:14px;border-radius:10px}.btn[disabled]{opacity:.4}
.group{background:var(--card);border-radius:14px;padding:12px;margin:10px 0}
input[type=text],input[type=password],textarea,select{font:inherit;font-size:16px;padding:10px 12px;border:1px solid var(--border);
border-radius:10px;background:var(--card);color:var(--text);width:100%;-webkit-appearance:none;appearance:none}
select{background-image:linear-gradient(45deg,transparent 50%,var(--muted) 50%),linear-gradient(135deg,var(--muted) 50%,transparent 50%);
background-position:calc(100% - 16px) 55%,calc(100% - 11px) 55%;background-size:5px 5px;background-repeat:no-repeat;padding-right:30px;width:auto}
textarea{min-height:90px}.grow{flex:1;min-width:130px}.muted{color:var(--muted);font-size:13.5px}
.list{display:flex;flex-direction:column;gap:8px}
.item{background:var(--card);border-radius:14px;padding:12px;display:flex;gap:12px;align-items:flex-start}
.item .main{flex:1;min-width:0}.item .nm{font-weight:600;word-break:break-word}.item .sub{font-size:13px;color:var(--muted);word-break:break-word;margin-top:2px}
.item .st{font-size:13.5px;font-weight:600;margin-top:4px}
.chk{width:26px;height:26px;flex:0 0 26px;border:2px solid #9ca3af;border-radius:50%;display:flex;align-items:center;justify-content:center;
color:#fff;font-weight:800;font-size:14px;cursor:pointer;margin-top:1px}.chk.on{background:var(--accent);border-color:var(--accent)}
.new{color:var(--accent)}.dl{color:var(--orange)}.done{color:var(--green)}.err{color:var(--red)}.skip{color:var(--muted)}
.fold{background:var(--card);border-radius:16px;padding:14px}
.fold .h{display:flex;gap:10px;align-items:flex-start}.fold .nm{font-weight:700;font-size:17px;flex:1;word-break:break-word}
.fold .acts{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.subs{margin-top:10px;background:var(--soft);border-radius:12px;padding:8px 12px}.subs summary{font-weight:600;color:var(--accent2);cursor:pointer;font-size:14px}
.subs a{display:block;padding:7px 0;border-top:1px solid var(--border);color:var(--accent);text-decoration:none;font-size:14px;word-break:break-all}
.subs a span{display:block;color:var(--muted);font-size:12px}
.sw{position:relative;width:51px;height:31px;flex:0 0 51px}.sw input{display:none}.sw span{position:absolute;inset:0;background:#d1d5db;border-radius:16px;transition:.2s}
.sw span:before{content:"";position:absolute;left:2px;top:2px;width:27px;height:27px;background:#fff;border-radius:50%;transition:.2s;box-shadow:0 2px 4px rgba(0,0,0,.2)}
.sw input:checked+span{background:#34c759}.sw input:checked+span:before{transform:translateX(20px)}
#log{background:var(--log);color:#cbd5e1;font:12px/1.5 ui-monospace,Menlo,Consolas,monospace;border-radius:12px;padding:10px;max-height:240px;overflow:auto;white-space:pre-wrap}
.prog{height:5px;background:var(--soft2);border-radius:3px;overflow:hidden;margin-top:6px}.prog i{display:block;height:100%;background:var(--accent);width:0;transition:width .4s}
.tabbar{position:fixed;left:0;right:0;bottom:0;z-index:40;display:flex;background:rgba(255,255,255,.92);backdrop-filter:saturate(180%) blur(18px);
-webkit-backdrop-filter:saturate(180%) blur(18px);border-top:.5px solid rgba(0,0,0,.15);padding-bottom:var(--sab)}
@media (prefers-color-scheme:dark){.tabbar{background:rgba(22,22,24,.92);border-top-color:rgba(255,255,255,.12)}}
.tabbar button{flex:1;height:var(--tab);border:0;background:none;font:inherit;font-size:11px;font-weight:600;color:var(--muted);display:flex;
flex-direction:column;align-items:center;justify-content:center;gap:3px;position:relative;cursor:pointer}
.tabbar button svg{width:26px;height:26px}.tabbar button.on{color:var(--accent)}
.tabbar .b{position:absolute;top:5px;left:calc(50% + 6px);min-width:18px;height:18px;padding:0 5px;border-radius:9px;background:var(--red);color:#fff;
font-size:11px;line-height:18px;text-align:center}.tabbar .b:empty{display:none}
.actbar{position:fixed;left:0;right:0;bottom:calc(var(--tab) + var(--sab));z-index:35;display:flex;gap:8px;padding:10px 12px;align-items:center;
background:rgba(255,255,255,.95);backdrop-filter:blur(14px);-webkit-backdrop-filter:blur(14px);border-top:.5px solid rgba(0,0,0,.12)}
@media (prefers-color-scheme:dark){.actbar{background:rgba(22,22,24,.95);border-top-color:rgba(255,255,255,.1)}}
.actbar .btn{flex:1}.actbar .muted{flex:0 0 auto}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.45);z-index:60;display:none;align-items:flex-end;justify-content:center}
.modal.on{display:flex}.sheet{background:var(--bg);width:100%;max-width:900px;height:calc(100% - var(--sat) - 12px);border-radius:14px 14px 0 0;
display:flex;flex-direction:column;animation:up .25s ease}.sheet.small{height:auto;max-height:90%}
@keyframes up{from{transform:translateY(40px);opacity:.4}to{transform:none;opacity:1}}
@media(min-width:700px){.modal{align-items:center}.sheet{border-radius:16px;height:auto;max-height:92vh}}
.sheet .hd{padding:14px 16px;display:flex;gap:10px;align-items:center;background:var(--card);border-radius:14px 14px 0 0;border-bottom:.5px solid var(--border)}
.sheet .hd b{flex:1;font-size:17px;word-break:break-word}.sheet .bd{padding:12px;overflow:auto;flex:1;-webkit-overflow-scrolling:touch}
.sheet .ft{padding:10px 12px calc(10px + var(--sab));border-top:.5px solid var(--border);background:var(--card);display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.sheet .ft .btn{flex:1;white-space:nowrap}
.toast{position:fixed;left:50%;bottom:calc(var(--tab) + var(--sab) + 80px);transform:translateX(-50%);background:rgba(17,24,39,.92);color:#fff;
padding:11px 16px;border-radius:12px;z-index:99;opacity:0;transition:.3s;max-width:92%;text-align:center;pointer-events:none}.toast.on{opacity:1}
.hide{display:none!important}.empty{text-align:center;color:var(--muted);padding:34px 10px}
#a2hs{background:var(--soft);border-radius:14px;padding:12px 14px;margin:10px 0;font-size:14px;display:flex;gap:10px;align-items:center}
#a2hs b{color:var(--accent)}
details summary{list-style:none;cursor:pointer}details summary::-webkit-details-marker{display:none}
</style></head><body>
<header><img src="/icon-180.png" alt=""><div><div class="t">Fshare Auto Download</div><div class="s" id="ver"></div></div>
<div class="r" id="hdr"></div></header>
<div id="ptr">↓ Kéo xuống để làm mới</div>
<main>
<div id="a2hs" class="hide"><div style="font-size:24px">⬆️</div><div class="grow">Cài như app: bấm <b>Chia sẻ</b> trong Safari →
<b>Thêm vào MH chính</b>.</div><button class="btn sm" onclick="dismissA2hs()">Đóng</button></div>

<section id="t-scan">
 <h2>Quét &amp; Tải</h2>
 <div class="cards">
  <div class="stat" style="--c:#6366f1"><b id="s-folders">-</b><span>Thư mục sẽ quét</span></div>
  <div class="stat" style="--c:var(--accent)"><b id="s-new">-</b><span>File mới chưa có</span></div>
  <div class="stat" style="--c:var(--orange)"><b id="s-dl">-</b><span>Đang tải</span></div>
  <div class="stat" style="--c:var(--green)"><b id="s-owned">-</b><span>Danh sách đã có</span></div>
 </div>
 <div class="group">
  <div style="display:flex;gap:8px"><button class="btn grow" id="btn-scan">⌕ Quét</button><button class="btn p grow" id="btn-go">⬇ Quét &amp; Tải</button>
  <button class="btn d" id="btn-stop" disabled>■</button></div>
  <div class="muted" id="status" style="margin-top:8px"></div><div class="prog"><i id="prog"></i></div>
  <div style="display:flex;align-items:center;gap:10px;margin-top:12px">
   <div class="grow"><div style="font-weight:600">Tự quét &amp; tải</div><div class="muted" id="auto-info"></div></div>
   <select id="interval"><option value="15">15 phút</option><option value="30">30 phút</option><option value="45">45 phút</option>
   <option value="60">60 phút</option><option value="90">90 phút</option><option value="120">120 phút</option></select>
   <label class="sw"><input type="checkbox" id="auto"><span></span></label></div>
 </div>
 <div style="display:flex;gap:8px;align-items:center;margin:4px 0 10px">
  <input type="text" class="grow" id="scan-q" placeholder="Tìm tên phim / file">
  <select id="scan-show"><option value="">Tất cả</option><option value="new">Mới</option><option value="dl">Đang tải</option>
  <option value="err">Lỗi</option><option value="skip">Chưa duyệt</option></select>
 </div>
 <div style="display:flex;gap:8px;margin-bottom:10px"><button class="btn sm" id="sel-all">Chọn tất cả</button><button class="btn sm" id="sel-none">Bỏ chọn</button></div>
 <div class="list" id="rows"></div>
 <details style="margin-top:16px"><summary class="muted">▸ Nhật ký</summary><div id="log" style="margin-top:8px"></div></details>
</section>

<section id="t-folders" class="hide">
 <h2>Thư mục</h2>
 <div style="display:flex;gap:8px"><button class="btn p grow" id="f-add">＋ Thêm thư mục</button><button class="btn grow" id="f-bulk">⧉ Dán nhiều link</button></div>
 <input type="text" id="f-q" placeholder="Tìm thư mục" style="margin:10px 0">
 <div class="muted" id="f-info" style="margin:0 4px 8px"></div>
 <div class="list" id="folders"></div>
</section>

<section id="t-history" class="hide">
 <h2>Lịch sử</h2>
 <input type="text" id="h-q" placeholder="Tìm" style="margin-bottom:10px">
 <div class="list" id="hist"></div>
</section>
</main>

<div class="actbar hide" id="scan-acts"><span class="muted" id="sel-count"></span>
 <button class="btn" id="btn-mark">☑ Đã có</button><button class="btn p" id="btn-dl">⬇ Tải</button></div>

<nav class="tabbar">
 <button data-t="scan" class="on"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>Quét &amp; Tải<span class="b" id="b-scan"></span></button>
 <button data-t="folders"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>Thư mục<span class="b" id="b-folders"></span></button>
 <button data-t="history"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></svg>Lịch sử<span class="b" id="b-hist"></span></button>
</nav>

<div class="modal" id="m-files"><div class="sheet">
 <div class="hd"><b id="mf-title"></b><button class="btn sm" onclick="closeFiles()">Xong</button></div>
 <div class="bd">
  <div class="muted" id="mf-info" style="margin:0 2px 10px"></div>
  <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
   <input type="text" class="grow" id="mf-q" placeholder="Tìm file">
   <select id="mf-show"><option value="">Tất cả</option><option value="no">Chưa có</option><option value="yes">Đã có</option></select></div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
   <button class="btn sm" onclick="mfPick(true)">Chọn tất cả</button><button class="btn sm" onclick="mfPick(false)">Bỏ chọn</button>
   <button class="btn sm" onclick="mfUntil()">↧ Chọn từ đầu đến file vừa chọn</button></div>
  <div class="list" id="mf-rows"></div>
 </div>
 <div class="ft"><div class="muted" id="mf-count" style="flex-basis:100%"></div>
  <button class="btn" onclick="mfMark(false)">↺ Bỏ</button><button class="btn p" onclick="mfMark(true)">☑ Đã có</button>
  <button class="btn g" id="mf-save" onclick="mfSave()">✓ Lưu</button></div>
</div></div>

<div class="modal" id="m-form"><div class="sheet small">
 <div class="hd"><button class="btn sm" onclick="hide('m-form')">Hủy</button><b id="ff-title" style="text-align:center"></b>
 <button class="btn sm p" id="ff-ok">Lưu</button></div>
 <div class="bd" id="ff-body"></div>
</div></div>

<div class="modal" id="m-login"><div class="sheet small" style="max-width:420px">
 <div class="hd"><b>Nhập mã PIN</b></div>
 <div class="bd"><p class="muted">Mã PIN đặt trong tab Cài đặt của tool trên máy tính.</p>
 <input type="password" id="pin" inputmode="numeric" autocomplete="current-password" placeholder="Mã PIN"></div>
 <div class="ft"><button class="btn p" onclick="login()">Vào</button></div>
</div></div>
<div class="toast" id="toast"></div>

<script>
const $=s=>document.querySelector(s), esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const norm=s=>String(s||'').normalize('NFD').replace(/[̀-ͯ]/g,'').replace(/đ/g,'d').replace(/Đ/g,'D').toLowerCase();
let tab='scan', ver={}, rows=[], sel=new Set(), seenRows=new Set(), folders=[], hist=[], mf=null;
try{tab=localStorage.getItem('tab')||'scan'}catch(e){tab='scan'}
async function api(path, body){
  const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json'},body:body?JSON.stringify(body):undefined,credentials:'same-origin'});
  let j={}; try{j=await r.json()}catch(e){}
  if(r.status==401){show('m-login');throw new Error('auth')}
  if(!r.ok) throw new Error(j.error||('Lỗi '+r.status)); return j;
}
function toast(t){const e=$('#toast');e.textContent=t;e.classList.add('on');clearTimeout(e._t);e._t=setTimeout(()=>e.classList.remove('on'),2600)}
function show(id){$('#'+id).classList.add('on');document.body.style.overflow='hidden'}
function hide(id){$('#'+id).classList.remove('on');if(!document.querySelector('.modal.on'))document.body.style.overflow=''}
async function act(fn,ok){try{const r=await fn(); if(ok) toast(typeof ok=='function'?ok(r):ok); return r}catch(e){if(e.message!='auth') toast('⚠ '+e.message)}}
async function login(){try{await api('/api/login',{pin:$('#pin').value});hide('m-login');refresh(true)}catch(e){toast('⚠ '+e.message)}}
$('#pin').addEventListener('keydown',e=>{if(e.key=='Enter')login()});
function setTab(t){tab=t; try{localStorage.setItem('tab',t)}catch(e){}
  document.querySelectorAll('.tabbar button').forEach(x=>x.classList.toggle('on',x.dataset.t==t));
  ['scan','folders','history'].forEach(x=>$('#t-'+x).classList.toggle('hide',x!=t)); window.scrollTo(0,0); refresh(true)}
document.querySelectorAll('.tabbar button').forEach(b=>b.onclick=()=>setTab(b.dataset.t));

// ---------- hướng dẫn cài như app (Safari iPhone chưa thêm vào màn hình chính)
const standalone=window.navigator.standalone||matchMedia('(display-mode: standalone)').matches;
const ios=/iphone|ipad|ipod/i.test(navigator.userAgent);
try{if(ios&&!standalone&&!localStorage.getItem('a2hs'))$('#a2hs').classList.remove('hide')}catch(e){}
function dismissA2hs(){$('#a2hs').classList.add('hide');try{localStorage.setItem('a2hs','1')}catch(e){}}

// ---------- kéo xuống để làm mới (app toàn màn hình không có nút tải lại)
let ty=null; const ptr=$('#ptr');
addEventListener('touchstart',e=>{ty=(window.scrollY<=0&&!document.querySelector('.modal.on'))?e.touches[0].clientY:null},{passive:true});
addEventListener('touchmove',e=>{if(ty==null)return; const d=e.touches[0].clientY-ty; if(d>0){ptr.style.height=Math.min(50,d/2)+'px';
  ptr.textContent=d>110?'↻ Thả ra để làm mới':'↓ Kéo xuống để làm mới'}},{passive:true});
addEventListener('touchend',async()=>{if(ty==null)return; const go=parseFloat(ptr.style.height||0)>=50; ty=null;
  if(go){ptr.textContent='Đang làm mới...';await refresh(true)} ptr.style.height='0'});

// ---------- trạng thái chung
async function refresh(force){
  let s; try{s=await api('/api/state')}catch(e){return}
  $('#ver').textContent=s.version; $('#hdr').innerHTML=(s.auto?('⏱ Quét lúc '+esc(s.next||'--')):'⏱ Tự quét: tắt')+'<br>'+esc(s.summary||'');
  $('#s-folders').textContent=s.stats.folders; $('#s-new').textContent=s.stats.new; $('#s-dl').textContent=s.stats.dl; $('#s-owned').textContent=s.stats.owned;
  $('#b-scan').textContent=s.stats.new||''; $('#b-hist').textContent=s.stats.dl||''; $('#b-folders').textContent=s.stats.unreviewed||'';
  $('#status').textContent=s.status||''; $('#prog').style.width=(s.progress[1]?Math.min(100,100*s.progress[0]/s.progress[1]):0)+'%';
  $('#btn-scan').disabled=$('#btn-go').disabled=s.busy; $('#btn-stop').disabled=!s.busy;
  if(document.activeElement!==$('#interval')){$('#auto').checked=s.auto;$('#interval').value=String(s.interval)}
  $('#auto-info').textContent=s.auto?('Lần tới: '+(s.next||'--')):'Đang tắt';
  const lg=$('#log'), atBottom=lg.scrollTop+lg.clientHeight>=lg.scrollHeight-10; lg.textContent=s.log.join('\n'); if(atBottom) lg.scrollTop=lg.scrollHeight;
  if(tab=='scan'&&(force||s.ver.rows!==ver.rows)) await loadRows();
  if(tab=='folders'&&(force||s.ver.folders!==ver.folders)) await loadFolders();
  if(tab=='history'&&(force||s.ver.hist!==ver.hist)) await loadHist();
  ver=s.ver; window._busy=s.busy; updSel();
}
setInterval(()=>{if(!document.hidden)refresh(false)},3000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden)refresh(true)});

// ---------- tab Quét & Tải
async function loadRows(){
  rows=await api('/api/rows'); const codes=new Set(rows.map(r=>r.code));
  if(!rows.length){sel.clear();seenRows.clear()}
  rows.forEach(r=>{if(!seenRows.has(r.code)){seenRows.add(r.code); if(r.tag=='new') sel.add(r.code)}});
  [...sel].forEach(c=>{if(!codes.has(c)) sel.delete(c)}); renderRows();
}
function renderRows(){
  const q=norm($('#scan-q').value), sh=$('#scan-show').value;
  const vis=rows.filter(r=>(!q||norm(r.folder+' '+r.name+' '+r.note).includes(q))&&(!sh||r.tag==sh));
  $('#rows').innerHTML=vis.length?vis.map(r=>`<div class="item" data-c="${r.code}"><div class="chk ${sel.has(r.code)?'on':''}">${sel.has(r.code)?'✓':''}</div>
   <div class="main"><div class="nm">${esc(r.name)}</div><div class="sub">${esc(r.folder)}${r.note?' · '+esc(r.note):''}</div>
   <div class="sub">${esc(r.date)} · ${esc(r.size)}</div><div class="st ${r.tag}">${esc(r.status)}</div></div></div>`).join('')
   :'<div class="empty">Chưa có kết quả.<br>Bấm "⌕ Quét" để tìm file mới.</div>';
  $('#rows').querySelectorAll('.item').forEach(e=>e.onclick=()=>{const c=e.dataset.c; sel.has(c)?sel.delete(c):sel.add(c); renderRows()});
  window._visRows=vis; updSel();
}
function updSel(){const n=sel.size; $('#sel-count').textContent=n?(n+' file'):''; $('#btn-dl').disabled=$('#btn-mark').disabled=!n||window._busy;
  $('#scan-acts').classList.toggle('hide',tab!='scan'||!n)}
$('#scan-q').oninput=renderRows; $('#scan-show').onchange=renderRows;
$('#sel-all').onclick=()=>{(window._visRows||[]).forEach(r=>sel.add(r.code));renderRows()};
$('#sel-none').onclick=()=>{(window._visRows||[]).forEach(r=>sel.delete(r.code));renderRows()};
const startScan=dl=>act(()=>api('/api/scan',{download:dl}),dl?'Đang quét & tải...':'Đang quét...').then(r=>{if(r){sel.clear();seenRows.clear();setTimeout(()=>refresh(true),800)}});
$('#btn-scan').onclick=()=>startScan(false); $('#btn-go').onclick=()=>startScan(true);
$('#btn-stop').onclick=()=>act(()=>api('/api/stop',{}),'Đã yêu cầu dừng');
$('#btn-dl').onclick=()=>act(()=>api('/api/download',{codes:[...sel]}),r=>'Đang tải '+r.count+' file').then(()=>setTimeout(()=>refresh(true),800));
$('#btn-mark').onclick=()=>act(()=>api('/api/mark',{codes:[...sel]}),'Đã thêm vào Danh sách đã có').then(()=>{sel.clear();refresh(true)});
$('#auto').onchange=$('#interval').onchange=()=>act(()=>api('/api/auto',{on:$('#auto').checked,interval:+$('#interval').value}),$('#auto').checked?'Đã bật tự quét':'Đã tắt tự quét').then(()=>refresh(false));

// ---------- tab Thư mục
async function loadFolders(){folders=await api('/api/folders'); renderFolders()}
function renderFolders(){
  const q=norm($('#f-q').value), vis=folders.filter(f=>!q||norm(f.name+' '+f.note+' '+f.url).includes(q));
  $('#f-info').textContent=folders.length+' thư mục · đã duyệt '+folders.filter(f=>f.reviewed).length;
  $('#folders').innerHTML=vis.length?vis.map(f=>{const code=(f.url.match(/folder\/(\w+)/)||[])[1]||'';
   return `<div class="fold"><div class="h"><div class="nm">${esc(f.name||'(chưa có tên)')}</div>
   <label class="sw"><input type="checkbox" ${f.enabled?'checked':''} data-tg="${code}"><span></span></label></div>
   ${f.note?`<div class="sub muted" style="margin-top:4px">✎ ${esc(f.note)}</div>`:''}
   <div style="color:${f.color};font-weight:600;font-size:14px;margin-top:8px">${esc(f.status)}</div>
   ${f.subs&&f.subs.length?`<details class="subs"><summary>Thư mục con (${f.subs.length})</summary>${f.subs.map(d=>
     `<a href="${esc(d.url)}" target="_blank" rel="noopener">↳ ${esc(d.path)}<span>${esc(d.url)}</span></a>`).join('')}</details>`:''}
   <div class="acts"><button class="btn p sm" data-sc="${code}">⌕ Quét file</button><button class="btn sm" data-ed="${code}">Sửa</button>
   <a class="btn sm" href="${esc(f.link)}" target="_blank" rel="noopener" style="text-decoration:none">↗ Fshare</a>
   <button class="btn d sm" data-del="${code}">Xóa</button></div></div>`}).join(''):'<div class="empty">Chưa có thư mục.<br>Bấm "＋ Thêm thư mục".</div>';
  $('#folders').querySelectorAll('[data-tg]').forEach(e=>e.onchange=()=>act(()=>api(`/api/folders/${e.dataset.tg}/update`,{enabled:e.checked})));
  $('#folders').querySelectorAll('[data-sc]').forEach(e=>e.onclick=()=>openFiles(e.dataset.sc));
  $('#folders').querySelectorAll('[data-ed]').forEach(e=>e.onclick=()=>editFolder(e.dataset.ed));
  $('#folders').querySelectorAll('[data-del]').forEach(e=>e.onclick=()=>{const f=folders.find(x=>x.url.includes(e.dataset.del));
   if(confirm('Xóa thư mục "'+(f&&f.name||e.dataset.del)+'"?')) act(()=>api(`/api/folders/${e.dataset.del}/update`,{delete:true}),'Đã xóa').then(loadFolders)});
}
$('#f-q').oninput=renderFolders;
function form(title, html, onok){$('#ff-title').textContent=title;$('#ff-body').innerHTML=html;$('#ff-ok').onclick=onok;show('m-form')}
const lab=t=>`<div class="muted" style="margin:10px 2px 4px">${t}</div>`;
$('#f-add').onclick=()=>form('Thêm thư mục',lab('Link thư mục Fshare *')+`<input type="text" id="fa-url" placeholder="https://www.fshare.vn/folder/..." autocapitalize="off" autocorrect="off">`
  +lab('Tên phim (để trống = tự lấy từ Fshare)')+`<input type="text" id="fa-name">`+lab('Ghi chú')+`<textarea id="fa-note"></textarea>`,
  ()=>act(()=>api('/api/folders/add',{url:$('#fa-url').value,name:$('#fa-name').value,note:$('#fa-note').value}),'Đã thêm').then(r=>{if(r){hide('m-form');loadFolders()}}));
$('#f-bulk').onclick=()=>form('Dán nhiều link',lab('Mỗi dòng 1 link. Có thể ghi tên trước: Tên phim | https://www.fshare.vn/folder/XXXX')
  +`<textarea id="fb-text" style="min-height:220px" autocapitalize="off" autocorrect="off"></textarea>`,
  ()=>act(()=>api('/api/folders/add',{bulk:$('#fb-text').value}),r=>'Đã thêm '+r.added+' thư mục').then(r=>{if(r){hide('m-form');loadFolders()}}));
function editFolder(code){const f=folders.find(x=>x.url.includes(code)); if(!f) return;
  form('Sửa thư mục',lab('Tên phim')+`<input type="text" id="fe-name" value="${esc(f.name)}">`+lab('Ghi chú')+`<textarea id="fe-note">${esc(f.note)}</textarea>`
  +lab('Chỉ lấy file có (phẩy ngăn cách)')+`<input type="text" id="fe-inc" value="${esc(f.include)}">`+lab('Bỏ qua file có')+`<input type="text" id="fe-exc" value="${esc(f.exclude)}">`,
  ()=>act(()=>api(`/api/folders/${code}/update`,{name:$('#fe-name').value,note:$('#fe-note').value,include:$('#fe-inc').value,exclude:$('#fe-exc').value}),'Đã lưu').then(r=>{if(r){hide('m-form');loadFolders()}}))}

// ---------- danh sách file của 1 thư mục
async function openFiles(code){
  mf={code,rows:[],picked:new Set(),lastPick:null}; $('#mf-title').textContent='Đang tải...'; $('#mf-info').textContent='Đang quét tất cả file trên Fshare...';
  $('#mf-rows').innerHTML=''; $('#mf-count').textContent=''; show('m-files');
  try{await api(`/api/folders/${code}/scan`,{})}catch(e){toast('⚠ '+e.message);return}
  for(let i=0;i<120;i++){await new Promise(r=>setTimeout(r,1000)); if(!mf||mf.code!=code) return;
    let r; try{r=await api(`/api/folders/${code}/files`)}catch(e){return}
    $('#mf-title').textContent=r.name;
    if(r.status=='error'){$('#mf-info').textContent='⚠ Lỗi đọc thư mục: '+r.error;return}
    if(r.status=='ok'){mf.rows=r.rows.map(x=>Object.assign(x,{orig:x.owned})); mf.reviewed=r.reviewed; renderFiles(); return}}
}
function mfChanges(){return mf?mf.rows.filter(r=>r.owned!==r.orig).length:0}
function closeFiles(){if(mfChanges()&&!confirm('Có '+mfChanges()+' thay đổi chưa lưu. Đóng mà không lưu?')) return; mf=null; hide('m-files'); loadFolders()}
function renderFiles(){
  const q=norm($('#mf-q').value), sh=$('#mf-show').value;
  const vis=mf.rows.filter(r=>(!q||norm(r.name).includes(q))&&(!sh||(sh=='yes')==r.owned)); mf.vis=vis;
  $('#mf-rows').innerHTML=vis.map(r=>{const p=mf.picked.has(r.code); let st,cl;
   if(r.owned&&!r.orig){st='✔ Đã có (chưa lưu)';cl='done'}else if(!r.owned&&r.orig){st='↺ Sẽ bỏ khỏi DS (chưa lưu)';cl='err'}
   else if(r.owned){st='✔ Đã có';cl='done'}else{st='● Chưa có';cl='new'}
   return `<div class="item" data-c="${r.code}"><div class="chk ${p?'on':''}">${p?'✓':''}</div><div class="main"><div class="nm">${esc(r.name)}</div>
   <div class="sub">${esc(r.date)} · ${esc(r.sizeh)}</div><div class="st ${cl}">${st}</div></div></div>`}).join('')||'<div class="empty">Không có file.</div>';
  $('#mf-rows').querySelectorAll('.item').forEach(e=>e.onclick=()=>{const c=e.dataset.c; if(mf.picked.has(c)) mf.picked.delete(c); else {mf.picked.add(c);mf.lastPick=c} renderFiles()});
  const own=mf.rows.filter(r=>r.owned).length, ch=mfChanges();
  $('#mf-info').textContent=mf.rows.length+' file · đã có '+own+' · chưa có '+(mf.rows.length-own)+'. Chạm để chọn file → "☑ Đã có" → "✓ Lưu". (↺ Bỏ = cho tải lại)';
  $('#mf-count').textContent='Đang chọn '+mf.picked.size+(ch?(' · '+ch+' thay đổi CHƯA LƯU'):'');
  $('#mf-save').disabled=!(ch||!mf.reviewed);
}
$('#mf-q').oninput=()=>mf&&renderFiles(); $('#mf-show').onchange=()=>mf&&renderFiles();
function mfPick(on){mf.vis.forEach(r=>on?mf.picked.add(r.code):mf.picked.delete(r.code)); renderFiles()}
function mfUntil(){if(!mf.lastPick){toast('Chạm chọn 1 file (VD: tập cuối bạn đã có) trước');return}
  const i=mf.vis.findIndex(r=>r.code==mf.lastPick); mf.vis.slice(0,i+1).forEach(r=>mf.picked.add(r.code)); renderFiles()}
function mfMark(on){mf.rows.forEach(r=>{if(mf.picked.has(r.code)) r.owned=on}); mf.picked.clear(); renderFiles()}
async function mfSave(){const add=mf.rows.filter(r=>r.owned&&!r.orig).map(r=>r.code), remove=mf.rows.filter(r=>!r.owned&&r.orig).map(r=>r.code);
  const r=await act(()=>api(`/api/folders/${mf.code}/save`,{add,remove}),r=>'Đã lưu: đã có '+r.owned+'/'+r.total);
  if(r){mf.rows.forEach(x=>x.orig=x.owned); mf.reviewed=true; renderFiles()}}

// ---------- tab Lịch sử
async function loadHist(){hist=await api('/api/history'); renderHist()}
function renderHist(){const q=norm($('#h-q').value), vis=hist.filter(h=>!q||norm(h.folder+' '+h.name).includes(q));
  $('#hist').innerHTML=vis.length?vis.map(h=>`<div class="item"><div class="main"><div class="nm">${esc(h.name)}</div>
   <div class="sub">${esc(h.folder)} · ${esc(h.time)}</div><div class="st ${h.tag}">${esc(h.status)}${h.pikpak?' · <span class="'+(h.upload=='error'?'err':h.upload=='done'?'done':'dl')+'">'+esc(h.pikpak)+'</span>':''}</div></div>
   ${h.can_upload?`<button class="btn sm" data-up="${h.code}">⇪ PikPak</button>`:''}</div>`).join(''):'<div class="empty">Chưa có lịch sử tải.</div>';
  $('#hist').querySelectorAll('[data-up]').forEach(e=>e.onclick=()=>act(()=>api('/api/history/upload',{codes:[e.dataset.up]}),r=>r.count?'Đã thêm vào hàng chờ upload':'Không upload được (file không còn trên máy?)').then(loadHist))}
$('#h-q').oninput=renderHist;
setTab(tab);
</script></body></html>"""


class App(tk.Tk):
    def __init__(self, auto=False, startup=False):
        super().__init__()
        self.title(APP_NAME)
        self.geometry(f"{min(1480, max(1100, self.winfo_screenwidth() - 60))}x800")
        self._normal_geom = None
        self._was_zoomed = False
        self.minsize(1040, 640)
        self.configure(bg=C_BG)
        self._set_icon()

        self.cfg = load_json(CONFIG_FILE, DEFAULT_CONFIG)
        self._no_save = False
        self._new_machine = self._init_machine_profile()
        if not self.cfg.get("firefox_path") or not os.path.isfile(self.cfg["firefox_path"]):
            self.cfg["firefox_path"] = find_firefox()
        if not self.cfg.get("watch_folders"):
            self.cfg["watch_folders"] = find_idm_folders()
        if self.cfg.get("close_firefox") not in CLOSE_FF_CHOICES:
            self.cfg["close_firefox"] = CLOSE_FF_CHOICES[0]
        if self.cfg.get("idm_window") not in IDM_WINDOW_CHOICES:
            self.cfg["idm_window"] = IDM_WINDOW_CHOICES[0]
        self.tray = None
        self.next_auto_ts = None
        self._tray_hint_shown = False
        self.history = load_json(HISTORY_FILE, {})
        self.present_codes, self.sub_of = set(), {}
        self._subs_open = set()
        self.owned = load_json(OWNED_FILE, None)
        if not isinstance(self.owned, dict):
            self.owned = self._migrate_owned()
        self.auto_var = tk.BooleanVar(value=bool(self.cfg.get("auto_scan", False)))
        self.interval_var = tk.StringVar(value=str(self.cfg.get("auto_interval", 60)))
        self.last_summary = ""
        self.q = queue.Queue()
        self.worker = None
        self.monitor_busy = False
        self.stop_flag = threading.Event()
        self.fp = None            # trạng thái khung "file của thư mục" bên tab Thư mục
        self.log_lines = deque(maxlen=300)
        self.rows_ver = self.hist_ver = self.folders_ver = 0
        self.web_files = {}
        self.web_srv = None

        self._style()
        self._install_edit_bindings()
        self._make_check_images()
        body = tk.Frame(self, bg=C_BG)
        body.pack(fill="both", expand=True)
        main = tk.Frame(body, bg=C_BG)
        self._build_header(main)
        self._build_statusbar(main)
        nb = ttk.Notebook(main, style="Side.TNotebook")
        self.nb = nb
        self.tab_scan = ttk.Frame(nb, style="Card.TFrame", padding=14)
        self.tab_folders = ttk.Frame(nb, style="Card.TFrame", padding=14)
        self.tab_settings = ttk.Frame(nb, style="Card.TFrame", padding=14)
        self.tab_history = ttk.Frame(nb, style="Card.TFrame", padding=14)
        for fr in (self.tab_scan, self.tab_folders, self.tab_history, self.tab_settings):
            nb.add(fr)
        self._build_sidebar(body)
        main.pack(side="left", fill="both", expand=True)
        nb.pack(fill="both", expand=True, padx=18, pady=(16, 0))

        self._build_scan()
        self._build_folders()
        self._build_settings()
        self._build_history()
        self.select_page("folders" if not any(f.get("reviewed") for f in self.cfg["folders"]) else "scan")
        if self.cfg.get("last_summary"):
            self.side_last.config(text="Quét lần cuối:\n" + self.cfg["last_summary"])
        self.update_stats()

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._restore_window()
        self.bind("<Configure>", self._track_geom, add="+")
        self._start_tray()
        self.after(100, self._poll)
        self.after(2000, self._monitor_tick)
        if self.cfg.get("update_check", True):
            self.after(15000, lambda: self.check_update(silent=True))
        self.after(4000, self._first_run_firewall)
        self.after(500, self._init_col_memory)
        if requests is None:
            messagebox.showerror(APP_NAME, "Thiếu thư viện 'requests'.\nChạy: pip install requests")
        if startup:
            if self.tray and self.cfg.get("tray_on_close", True):
                self._was_zoomed = bool((self.cfg.get("window") or {}).get("zoomed"))
                self.withdraw()
            if self.cfg.get("startup_scan", True):
                self.next_auto_ts = time.time() + 60
                self.log("Khởi động cùng Windows: sẽ quét sau 1 phút.")
        if auto:
            self.after(800, lambda: self.start_scan(download=True))
        self.after(1000, self._auto_tick)
        self.up_q = queue.Queue()
        self.up_queued = set()          # file đang nằm trong hàng chờ / đang upload (tránh upload 2 lần)
        threading.Thread(target=self._uploader_loop, daemon=True).start()
        self._resume_uploads()
        self.start_web()
        self._backup_pending = False
        self.after(1500, lambda: self.check_pikpak(silent=True))
        if self._new_machine:
            self.after(900, self._new_machine_dialog)

    # ================================================================ DANH SÁCH ĐÃ CÓ
    def _migrate_owned(self):
        """Lần đầu dùng v4: chuyển các tập 'Xong' trong lịch sử cũ sang Danh sách đã có."""
        name2code = {f.get("name", ""): get_code(f.get("url")) for f in self.cfg["folders"]}
        owned = {}
        for code, h in self.history.items():
            if h.get("status") == STATUS_DONE:
                owned[code] = {"folder": name2code.get(h.get("folder", ""), "") or "",
                               "fname": h.get("folder", ""), "name": h.get("name", ""), "size": h.get("size"),
                               "ts": h.get("ts", time.time()), "src": "migrate"}
        save_json(OWNED_FILE, owned)
        return owned

    def note_listing(self, files):
        """Ghi nhớ mã file + thư mục con của danh sách vừa quét (dùng cho so khớp Danh sách đã có)."""
        for it in files:
            c = it.get("linkcode")
            if c:
                self.present_codes.add(c)
                self.sub_of[c] = it.get("_sub", "")

    def owned_index(self):
        """(tập mã file, tập (mã thư mục, thư mục con, tên chuẩn hóa)) để tra nhanh.
        So theo TÊN chỉ dùng cho file đã có mà mã cũ không còn trên Fshare (bị up lại) - tránh việc
        2 tập trùng tên ở 2 thư mục con (Season 1 / Season 2) bị coi là một -> quét thiếu."""
        codes = set(self.owned)
        present = self.present_codes
        names = set()
        for c, v in list(self.owned.items()):
            if c in present:
                continue
            names.add((v.get("folder", ""), v.get("sub"), name_key(v.get("name", ""))))
        return codes, names

    def is_owned(self, idx, fcode, folder_code, name):
        codes, names = idx
        if fcode in codes:
            return True
        k = name_key(name)
        sub = self.sub_of.get(fcode)
        return ((folder_code, sub, k) in names if sub is not None else False) \
            or (folder_code, None, k) in names or ("", None, k) in names

    def add_owned(self, fcode, folder_code, fname, name, size, src):
        rec = {"folder": folder_code or "", "fname": fname, "name": name, "size": size,
               "ts": time.time(), "src": src}
        if fcode in self.sub_of:
            rec["sub"] = self.sub_of[fcode]
        self.owned[fcode] = rec

    def remove_owned(self, fcode, folder_code, name):
        k = name_key(name)
        sub = self.sub_of.get(fcode)
        for c in [c for c, v in self.owned.items()
                  if c == fcode or (c not in self.present_codes and v.get("folder", "") in (folder_code, "")
                                    and name_key(v.get("name", "")) == k
                                    and (v.get("sub") is None or v.get("sub") == sub))]:
            self.owned.pop(c, None)

    def save_owned(self):
        save_json(OWNED_FILE, self.owned)
        self.update_stats()

    # ================================================================ GIAO DIỆN CHUNG
    def _style(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        base = tkfont.Font(family=FONT, size=10)
        self.row_h = max(30, base.metrics("linespace") + 14)     # dòng bảng đủ cao, không cắt chữ
        st.configure(".", background=C_BG, foreground=C_TEXT, font=(FONT, 10), bordercolor=C_BORDER)
        st.configure("Card.TFrame", background=C_CARD)
        st.configure("Bg.TFrame", background=C_BG)
        st.configure("TLabel", background=C_CARD, foreground=C_TEXT)
        st.configure("Muted.TLabel", background=C_CARD, foreground=C_MUTED, font=(FONT, 9))
        st.configure("Section.TLabel", background=C_CARD, foreground=C_TEXT, font=(FONT, 12, "bold"))
        st.configure("Status.TLabel", background=C_BG, foreground=C_MUTED, font=(FONT, 9))
        st.configure("TCheckbutton", background=C_CARD)
        st.map("TCheckbutton", background=[("active", C_CARD)])
        st.configure("Side.TNotebook", background=C_BG, borderwidth=0, tabmargins=(0, 0, 0, 0))
        st.layout("Side.TNotebook.Tab", [])
        st.configure("TButton", padding=(12, 6), background="#edf0f3", foreground=C_TEXT,
                     borderwidth=0, focusthickness=0, font=(FONT, 10))
        st.map("TButton", background=[("active", "#e1e6eb"), ("disabled", "#f3f4f6")],
               foreground=[("disabled", "#9ca3af")])
        st.configure("Small.TButton", padding=(8, 4), font=(FONT, 9))
        st.configure("Accent.TButton", background=C_ACCENT, foreground="white", font=(FONT, 10, "bold"),
                     padding=(14, 7))
        st.map("Accent.TButton", background=[("active", C_ACCENT_HOVER), ("disabled", C_ACCENT_DIS)],
               foreground=[("disabled", "#effaf8")])
        st.configure("AccentSmall.TButton", background=C_ACCENT, foreground="white", font=(FONT, 9, "bold"),
                     padding=(10, 4))
        st.map("AccentSmall.TButton", background=[("active", C_ACCENT_HOVER), ("disabled", C_ACCENT_DIS)])
        st.configure("Success.TButton", background=C_GREEN, foreground="white", font=(FONT, 10, "bold"),
                     padding=(16, 7))
        st.map("Success.TButton", background=[("active", "#15803d"), ("disabled", "#a7dcb8")],
               foreground=[("disabled", "#ecfdf5")])
        st.configure("Danger.TButton", background="#fee2e2", foreground=C_RED)
        st.map("Danger.TButton", background=[("active", "#fecaca"), ("disabled", "#f3f4f6")],
               foreground=[("disabled", "#d1d5db")])
        st.configure("DangerSmall.TButton", background="#fee2e2", foreground=C_RED, padding=(8, 4), font=(FONT, 9))
        st.map("DangerSmall.TButton", background=[("active", "#fecaca")])
        st.configure("TEntry", fieldbackground="white", bordercolor=C_BORDER, lightcolor=C_BORDER,
                     darkcolor=C_BORDER, padding=6)
        st.map("TEntry", bordercolor=[("focus", C_ACCENT)], lightcolor=[("focus", C_ACCENT)])
        st.configure("TCombobox", fieldbackground="white", bordercolor=C_BORDER, lightcolor=C_BORDER,
                     darkcolor=C_BORDER, padding=5, arrowsize=12)
        st.map("TCombobox", fieldbackground=[("readonly", "white")])
        st.configure("TSpinbox", fieldbackground="white", bordercolor=C_BORDER, lightcolor=C_BORDER,
                     darkcolor=C_BORDER, padding=4, arrowsize=12)
        st.configure("Pro.Treeview", background="white", fieldbackground="white", foreground=C_TEXT,
                     rowheight=self.row_h, borderwidth=0, font=(FONT, 10))
        st.configure("Pro.Treeview.Heading", background=C_HEAD, foreground=C_MUTED,
                     font=(FONT, 9, "bold"), borderwidth=0, padding=(8, 8), relief="flat")
        st.map("Pro.Treeview.Heading", background=[("active", "#e6eaee")])
        st.map("Pro.Treeview", background=[("selected", C_SEL)], foreground=[("selected", C_TEXT)])
        st.layout("Pro.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        st.configure("Pro.Horizontal.TProgressbar", troughcolor="#e6eaee", background=C_ACCENT,
                     bordercolor="#e6eaee", lightcolor=C_ACCENT, darkcolor=C_ACCENT, thickness=6)
        st.configure("Vertical.TScrollbar", background="#e6eaee", troughcolor=C_CARD, borderwidth=0, arrowsize=12)
        st.configure("TPanedwindow", background=C_CARD)
        st.configure("Sash", sashthickness=8, gripcount=0, background=C_BORDER)

    def _tree(self, parent, cols, heads, widths, anchors=None, height=12, check=False, stretch=None):
        wrap = ttk.Frame(parent, style="Card.TFrame")
        border = tk.Frame(wrap, bg=C_BORDER, padx=1, pady=1)
        border.pack(fill="both", expand=True)
        inner = tk.Frame(border, bg="white")
        inner.pack(fill="both", expand=True)
        tree = ttk.Treeview(inner, columns=cols, show=("tree headings" if check else "headings"),
                            style="Pro.Treeview", height=height)
        if check:
            tree.column("#0", width=46, minwidth=46, stretch=False, anchor="center")
        for i, (c, t, w) in enumerate(zip(cols, heads, widths)):
            tree.heading(c, text=t, anchor="w")
            st_ = (c in stretch) if stretch else (w >= 300)
            tree.column(c, width=w, minwidth=min(w, 60), anchor=(anchors[i] if anchors else "w"), stretch=st_)
        sb = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        tree.tag_configure("odd", background="#f9fafb")
        tree.tag_configure("new", foreground=C_BLUE)
        tree.tag_configure("dl", foreground=C_ORANGE)
        tree.tag_configure("done", foreground=C_GREEN)
        tree.tag_configure("err", foreground=C_RED)
        tree.tag_configure("skip", foreground="#9ca3af")
        return wrap, tree

    def _build_sidebar(self, parent):
        self.side_w = max(SIDE_MIN, min(SIDE_MAX, int(self.cfg.get("side_w", SIDE_W) or SIDE_W)))
        side = tk.Frame(parent, bg=C_SIDE, width=self.side_w)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        self.side = side
        W = self.side_w - 64          # độ rộng chữ trong các thẻ

        # ----- logo + tên app + nút thu gọn
        top = tk.Frame(side, bg=C_SIDE)
        top.pack(fill="x", padx=(18, 10), pady=(16, 10))
        self.side_brand = tk.Frame(top, bg=C_SIDE)
        self.side_brand.pack(side="left")
        if self.icon_small:
            tk.Label(self.side_brand, image=self.icon_small, bg=C_SIDE).pack(side="left", padx=(0, 10))
        bt = tk.Frame(self.side_brand, bg=C_SIDE)
        bt.pack(side="left")
        tk.Label(bt, text=APP_TITLE, bg=C_SIDE, fg="white", font=(FONT, 12, "bold")).pack(anchor="w")
        tk.Label(bt, text=APP_VERSION, bg=C_SIDE,
                 fg=C_SIDE_MUTED, font=(FONT, 8)).pack(anchor="w")
        self.side_toggle = tk.Label(top, text="«", bg=C_SIDE, fg=C_SIDE_TEXT, font=(FONT, 15, "bold"),
                                    cursor="hand2", padx=8)
        self.side_toggle.pack(side="right")
        self.side_toggle.bind("<Button-1>", lambda e: self.toggle_sidebar())
        self.side_toggle.bind("<Enter>", lambda e: self.side_toggle.config(bg=C_SIDE_HOVER))
        self.side_toggle.bind("<Leave>", lambda e: self.side_toggle.config(bg=C_SIDE))
        self.bind_all("<Control-b>", lambda e: self.toggle_sidebar())
        tk.Frame(side, bg=C_SIDE_HOVER, height=1).pack(fill="x", padx=14, pady=(0, 8))

        self.side_menu_lbl = tk.Label(side, text="MENU", bg=C_SIDE, fg=C_SIDE_MUTED, font=(FONT, 8, "bold"))
        self.side_menu_lbl.pack(anchor="w", padx=24, pady=(4, 4))
        self.nav = {}
        items = (("folders", "▦", "Thư mục Fshare", self.tab_folders),
                 ("scan", "⬇", "Quét & Tải", self.tab_scan),
                 ("history", "☰", "Lịch sử tải", self.tab_history),
                 ("settings", "⚙", "Cài đặt", self.tab_settings))
        for n, (key, icon, text, frame) in enumerate(items, 1):
            row = tk.Frame(side, bg=C_SIDE, cursor="hand2")
            row.pack(fill="x", padx=12, pady=2)
            bar = tk.Frame(row, bg=C_SIDE, width=3)
            bar.pack(side="left", fill="y")
            ic = tk.Label(row, text=icon, bg=C_SIDE, fg=C_SIDE_MUTED, font=(FONT, 13), width=2)
            ic.pack(side="left", padx=(10, 8), pady=10)
            lb = tk.Label(row, text=text, bg=C_SIDE, fg=C_SIDE_TEXT, font=(FONT, 11, "bold"))
            lb.pack(side="left")
            badge = tk.Label(row, text="", bg=C_ACCENT, fg="white", font=(FONT, 9, "bold"), padx=7)
            self.nav[key] = {"row": row, "bar": bar, "icon": ic, "label": lb, "badge": badge, "frame": frame}
            for w in (row, ic, lb, badge):
                w.bind("<Button-1>", lambda e, k=key: self.select_page(k))
                w.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
                w.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self.bind_all(f"<Control-Key-{n}>", lambda e, k=key: self.select_page(k))

        def card(parent_, title, **pack):
            outer = tk.Frame(parent_, bg=C_SIDE_CARD, padx=14, pady=10)
            outer.pack(fill="x", **pack)
            tk.Label(outer, text=title, bg=C_SIDE_CARD, fg=C_SIDE_MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
            return outer

        self.side_lib = card(side, "THƯ VIỆN", padx=14, pady=(16, 0))
        self.side_owned = tk.Label(self.side_lib, text="", bg=C_SIDE_CARD, fg="#86efac", font=(FONT, 10),
                                   anchor="w", justify="left", wraplength=W)
        self.side_owned.pack(fill="x", pady=(4, 0))

        bottom = tk.Frame(side, bg=C_SIDE)
        bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 14))
        self.side_bottom = bottom
        ca = card(bottom, "TỰ ĐỘNG", pady=(0, 10))
        r = tk.Frame(ca, bg=C_SIDE_CARD)
        r.pack(fill="x", pady=(6, 2))
        tk.Checkbutton(r, text="Tự quét mỗi", variable=self.auto_var, command=self._auto_changed,
                       bg=C_SIDE_CARD, fg=C_SIDE_TEXT, selectcolor=C_SIDE_HOVER, activebackground=C_SIDE_CARD,
                       activeforeground="white", font=(FONT, 10), bd=0, highlightthickness=0).pack(side="left")
        cb = ttk.Combobox(r, textvariable=self.interval_var, values=INTERVAL_CHOICES, width=4, state="readonly")
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._auto_changed())
        tk.Label(r, text="phút", bg=C_SIDE_CARD, fg=C_SIDE_TEXT, font=(FONT, 10)).pack(side="left")
        self.side_auto = tk.Label(ca, text="", bg=C_SIDE_CARD, fg=C_SIDE_ACTIVE, font=(FONT, 10), anchor="w")
        self.side_auto.pack(fill="x", pady=(4, 0))
        self.side_last = tk.Label(ca, text="Chưa quét lần nào", bg=C_SIDE_CARD, fg=C_SIDE_MUTED, font=(FONT, 9),
                                  anchor="w", justify="left", wraplength=W)
        self.side_last.pack(fill="x", pady=(2, 0))

        ck = card(bottom, "KẾT NỐI", pady=(0, 10))
        self.side_pk = tk.Label(ck, text="☁ PikPak: đang kiểm tra...", bg=C_SIDE_CARD, fg=C_SIDE_MUTED,
                                font=(FONT, 10), anchor="w", justify="left", wraplength=W, cursor="hand2")
        self.side_pk.pack(fill="x", pady=(4, 0))
        self.side_pk.bind("<Button-1>", lambda e: (self.select_page("settings"), self._settings_page("store")))
        self.side_bk = tk.Label(ck, text="", bg=C_SIDE_CARD, fg=C_SIDE_MUTED, font=(FONT, 9), anchor="w",
                                justify="left", wraplength=W)
        self.side_bk.pack(fill="x")
        if self.cfg.get("last_backup"):
            self.side_bk.config(text="⟲ Sao lưu: " + self.cfg["last_backup"])
        self.side_web = tk.Label(ck, text="", bg=C_SIDE_CARD, fg="#7dd3c8", font=(FONT, 10), anchor="w",
                                 justify="left", wraplength=W, cursor="hand2")
        self.side_web.pack(fill="x", pady=(2, 0))
        self.side_web.bind("<Button-1>", lambda e: self._open_web_local())
        tk.Label(ck, text=f"⌂ Máy: {MACHINE}", bg=C_SIDE_CARD, fg=C_SIDE_MUTED, font=(FONT, 9),
                 anchor="w").pack(fill="x", pady=(2, 0))
        self.side_foot = tk.Label(bottom, text="Kéo mép phải thanh này để đổi độ rộng  ·  Ctrl+B / bấm « để thu gọn",
                                  bg=C_SIDE, fg="#4b5561", font=(FONT, 8), anchor="w", justify="left", wraplength=W)
        self.side_foot.pack(fill="x", padx=2)
        self._side_wraps = [self.side_owned, self.side_last, self.side_pk, self.side_bk, self.side_web, self.side_foot]
        # vạch kéo đổi độ rộng thanh bên
        grip = tk.Frame(parent, bg="#0a0d10", width=5, cursor="sb_h_double_arrow")
        grip.pack(side="left", fill="y")
        grip.bind("<Enter>", lambda e: grip.config(bg=C_ACCENT))
        grip.bind("<Leave>", lambda e: grip.config(bg="#0a0d10"))
        grip.bind("<B1-Motion>", self._side_drag)
        grip.bind("<ButtonRelease-1>", self._side_drag_end)
        grip.bind("<Double-Button-1>", lambda e: self.toggle_sidebar())
        self.side_grip = grip
        if self.cfg.get("side_collapsed"):
            self.after(10, lambda: self.toggle_sidebar(True))

    def _side_drag(self, e):
        w = e.x_root - self.side.winfo_rootx()
        if getattr(self, "side_collapsed", False):
            if w < SIDE_MIN:
                return
            self.toggle_sidebar(False)
        w = max(SIDE_MIN, min(SIDE_MAX, w))
        if w == self.side_w:
            return
        self.side_w = w
        self.side.config(width=w)
        for lb in self._side_wraps:
            lb.config(wraplength=w - 64)

    def _side_drag_end(self, e):
        self.cfg["side_w"] = self.side_w
        try:
            disk = load_json(CONFIG_FILE, {})
            if isinstance(disk, dict) and disk:
                disk["side_w"] = self.side_w
                save_json(CONFIG_FILE, disk)
        except Exception:
            pass

    def toggle_sidebar(self, collapse=None):
        """Thu gọn / mở rộng thanh bên trái (Ctrl+B)."""
        if collapse is None:
            collapse = not getattr(self, "side_collapsed", False)
        self.side_collapsed = collapse
        self.side.config(width=SIDE_W_MINI if collapse else self.side_w)
        if collapse:
            self.side_brand.pack_forget()
            self.side_menu_lbl.pack_forget()
            self.side_toggle.config(text="»")
            self.side_toggle.master.pack_configure(padx=(6, 6))
            for n in self.nav.values():
                n["label"].pack_forget()
                n["badge"].pack_forget()
            self.side_lib.pack_forget()
            self.side_bottom.pack_forget()
        else:
            self.side_toggle.config(text="«")
            self.side_toggle.master.pack_configure(padx=(18, 10))
            self.side_brand.pack(side="left")
            self.side_menu_lbl.pack(anchor="w", padx=24, pady=(4, 4), before=self.nav["folders"]["row"])
            for n in self.nav.values():
                n["label"].pack(side="left")
                if n["badge"].cget("text"):
                    n["badge"].pack(side="right", padx=10)
            self.side_bottom.pack(side="bottom", fill="x", padx=14, pady=(0, 14))
            self.side_lib.pack(fill="x", padx=14, pady=(16, 0))
        if self.cfg.get("side_collapsed") != collapse:
            self.cfg["side_collapsed"] = collapse
            try:   # chỉ ghi cờ này, không đụng các ô Cài đặt đang sửa dở
                disk = load_json(CONFIG_FILE, {})
                if isinstance(disk, dict) and disk:
                    disk["side_collapsed"] = collapse
                    save_json(CONFIG_FILE, disk)
            except Exception:
                pass

    def update_stats(self):
        dl = sum(1 for h in self.history.values() if h.get("status") in WATCH_STATUSES)
        on = [f for f in self.cfg["folders"] if f.get("enabled", True)]
        unreviewed = sum(1 for f in on if not f.get("reviewed"))
        if hasattr(self, "stat_vars"):
            self.stat_vars["folders"].set(f"{len(on)}/{len(self.cfg['folders'])}")
            self.stat_vars["new"].set(str(getattr(self, "_new_count", 0)))
            self.stat_vars["dl"].set(str(dl))
            self.stat_vars["owned"].set(str(len(self.owned)))
        if hasattr(self, "nav"):
            self.set_badge("scan", getattr(self, "_new_count", 0))
            self.set_badge("history", dl, C_ORANGE)
            bad = sum(1 for f in on if f.get("last_error"))
            self.set_badge("folders", bad or unreviewed, C_RED if bad else C_ORANGE)
            self.side_owned.config(text=f"✔ Danh sách đã có: {len(self.owned)} file"
                                   + (f"\n◔ {unreviewed} thư mục chưa duyệt" if unreviewed else ""))

    # ================================================================ TAB THƯ MỤC FSHARE
    def _build_folders(self):
        t = self.tab_folders
        pw = ttk.Panedwindow(t, orient="horizontal")
        pw.pack(fill="both", expand=True)
        left = ttk.Frame(pw, style="Card.TFrame")
        right = ttk.Frame(pw, style="Card.TFrame")
        pw.add(left, weight=0)
        pw.add(right, weight=1)
        self._sash_set = False

        def place_sash(e):
            if not self._sash_set and e.width > 700:
                self._sash_set = True
                self.after(50, lambda: pw.sashpos(0, max(440, int(e.width * 0.43))))
        pw.bind("<Configure>", place_sash, add="+")

        # ----- cột trái: danh sách thư mục
        top = ttk.Frame(left, style="Card.TFrame")
        top.pack(fill="x", padx=(0, 12))
        ttk.Label(top, text="Thư mục Fshare", style="Section.TLabel").pack(side="left")
        self.folder_count = tk.StringVar()
        ttk.Label(top, textvariable=self.folder_count, style="Muted.TLabel").pack(side="left", padx=10)
        b = ttk.Frame(left, style="Card.TFrame")
        b.pack(fill="x", pady=(10, 6), padx=(0, 12))
        ttk.Button(b, text="＋  Thêm thư mục", style="Accent.TButton",
                   command=lambda: self.open_folder_dialog()).pack(side="left")
        ttk.Button(b, text="⧉  Dán nhiều link", command=self.folder_bulk).pack(side="left", padx=8)
        mb = ttk.Menubutton(b, text="⋯  Khác")
        menu = tk.Menu(mb, tearoff=0, font=(FONT, 10))
        menu.add_command(label="Chọn quét tất cả thư mục", command=lambda: self.folder_check_all(True))
        menu.add_command(label="Bỏ quét tất cả thư mục", command=lambda: self.folder_check_all(False))
        menu.add_separator()
        menu.add_command(label="Xuất danh sách thư mục...", command=self.folder_export)
        menu.add_command(label="Nhập danh sách thư mục...", command=self.folder_import)
        mb["menu"] = menu
        mb.pack(side="left")
        sr = ttk.Frame(left, style="Card.TFrame")
        sr.pack(fill="x", pady=(0, 8), padx=(0, 12))
        ttk.Label(sr, text="⌕ Tìm", style="Muted.TLabel").pack(side="left")
        self.folder_search_var = tk.StringVar()
        ttk.Entry(sr, textvariable=self.folder_search_var).pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.folder_search_var.trace_add("write", lambda *a: self.refresh_folders())

        holder = tk.Frame(left, bg=C_BG, highlightbackground=C_BORDER, highlightthickness=1)
        holder.pack(fill="both", expand=True, padx=(0, 12))
        self.card_canvas = tk.Canvas(holder, bg=C_BG, highlightthickness=0, bd=0)
        vs = ttk.Scrollbar(holder, orient="vertical", command=self.card_canvas.yview)
        self.card_canvas.configure(yscrollcommand=vs.set)
        vs.pack(side="right", fill="y")
        self.card_canvas.pack(side="left", fill="both", expand=True)
        self.card_inner = tk.Frame(self.card_canvas, bg=C_BG)
        self._card_win = self.card_canvas.create_window((0, 0), window=self.card_inner, anchor="nw")
        self.card_inner.bind("<Configure>", lambda e: self.card_canvas.configure(
            scrollregion=self.card_canvas.bbox("all")))
        self.card_canvas.bind("<Configure>", self._on_cards_resize)
        for w in (self.card_canvas, self.card_inner):
            w.bind("<Enter>", lambda e: self.bind_all("<MouseWheel>", self._cards_wheel))
            w.bind("<Leave>", lambda e: self.unbind_all("<MouseWheel>"))
        self._wrap_labels = []
        self.active_folder = None

        # ----- cột phải: file của thư mục đang chọn
        self._build_file_panel(right)
        self.refresh_folders()

    def _cards_wheel(self, e):
        self.card_canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")

    def _on_cards_resize(self, e):
        self.card_canvas.itemconfigure(self._card_win, width=e.width)
        for lb in self._wrap_labels:
            try:
                lb.configure(wraplength=max(200, e.width - 56))
            except tk.TclError:
                pass

    def _folder_status(self, f):
        """(chữ, màu) trạng thái của 1 thư mục."""
        if not f.get("enabled", True):
            return "Tạm bỏ qua khi quét", C_MUTED
        if f.get("last_error"):
            return "⚠  Lỗi lần quét trước: " + str(f["last_error"])[:70], C_RED
        if not f.get("reviewed"):
            return "◔  Chưa duyệt — bấm \"Quét file\" để chọn những file đã có", C_ORANGE
        when = ""
        if f.get("last_scan"):
            when = " · quét " + dt.datetime.fromtimestamp(float(f["last_scan"])).strftime("%H:%M %d/%m")
        total, owned = int(f.get("total", 0) or 0), int(f.get("owned", 0) or 0)
        new = max(0, total - owned)
        if new:
            return f"●  {new} file mới chưa có  ·  đã có {owned}/{total}{when}", C_BLUE
        return f"✔  Đã có đủ {owned}/{total} file{when}", C_GREEN

    def refresh_folders(self):
        for w in self.card_inner.winfo_children():
            w.destroy()
        self._wrap_labels = []
        q = strip_accents(self.folder_search_var.get().strip()) if hasattr(self, "folder_search_var") else ""
        width = max(300, self.card_canvas.winfo_width() - 56)
        shown = 0
        for i, f in enumerate(self.cfg["folders"]):
            text = f"{f.get('name', '')} {f.get('note', '')} {f.get('url', '')}"
            if q and q not in strip_accents(text):
                continue
            shown += 1
            active = self.active_folder == i
            card = tk.Frame(self.card_inner, bg=C_CARD, highlightthickness=2 if active else 1,
                            highlightbackground=C_ACCENT if active else C_BORDER, padx=12, pady=10)
            card.pack(fill="x", padx=8, pady=(8, 0))
            r1 = tk.Frame(card, bg=C_CARD)
            r1.pack(fill="x")
            on = f.get("enabled", True)
            chk = tk.Label(r1, image=self.img_on if on else self.img_off, bg=C_CARD, cursor="hand2")
            chk.pack(side="left", anchor="n", pady=2)
            chk.bind("<Button-1>", lambda e, i=i: self._toggle_folder(i))
            tk.Label(r1, text=f"#{i + 1}", bg=C_CARD, fg=C_MUTED, font=(FONT, 9)).pack(side="right", anchor="n")
            name = tk.Label(r1, text=f.get("name") or "(chưa có tên — sẽ lấy từ Fshare)", bg=C_CARD,
                            fg=C_TEXT if on else C_MUTED, font=(FONT, 11, "bold"), anchor="w", justify="left",
                            wraplength=width - 40)
            name.pack(side="left", fill="x", expand=True, padx=(8, 0))
            self._wrap_labels.append(name)
            link = tk.Label(card, text=f.get("url", ""), bg=C_CARD, fg=C_MUTED, font=(FONT, 9), anchor="w",
                            justify="left", wraplength=width, cursor="hand2")
            link.pack(fill="x", pady=(4, 0))
            self._link_label(link, f.get("url", ""), C_MUTED)
            self._wrap_labels.append(link)
            if f.get("note"):
                nt = tk.Label(card, text="✎  " + one_line(f["note"]), bg=C_CARD, fg="#3d4752", font=(FONT, 9),
                              anchor="w", justify="left", wraplength=width)
                nt.pack(fill="x", pady=(2, 0))
                self._wrap_labels.append(nt)
            subs = f.get("subs") or []
            if subs:
                code_f = get_code(f.get("url"))
                expanded = code_f in self._subs_open
                box = tk.Frame(card, bg=C_SOFT, padx=8, pady=5)
                box.pack(fill="x", pady=(6, 0))
                hd = tk.Label(box, text=("▾" if expanded else "▸") + f"  Thư mục con ({len(subs)})"
                              + ("" if expanded else "  —  bấm để xem link"),
                              bg=C_SOFT, fg=C_ACCENT_HOVER, font=(FONT, 9, "bold"), anchor="w", cursor="hand2")
                hd.pack(fill="x")
                hd.bind("<Button-1>", lambda e, c=code_f: self._toggle_subs(c))
                if expanded:
                    for d in subs[:60]:
                        u = folder_link(d.get("code"))
                        ln = tk.Label(box, text=f"↳ {d.get('path')}   {u}", bg=C_SOFT, fg=C_BLUE, font=(FONT, 9),
                                      anchor="w", justify="left", wraplength=width - 20, cursor="hand2")
                        ln.pack(fill="x", pady=(2, 0))
                        self._link_label(ln, u, C_BLUE)
                    if len(subs) > 60:
                        tk.Label(box, text=f"... và {len(subs) - 60} thư mục con khác", bg=C_SOFT, fg=C_MUTED,
                                 font=(FONT, 9), anchor="w").pack(fill="x")
                    tk.Label(box, text="Bấm: mở trên Firefox  ·  Chuột phải: copy link", bg=C_SOFT, fg=C_MUTED,
                             font=(FONT, 8), anchor="w").pack(fill="x", pady=(3, 0))
            stext, scol = self._folder_status(f)
            stl = tk.Label(card, text=stext, bg=C_CARD, fg=scol, font=(FONT, 9, "bold"), anchor="w",
                           justify="left", wraplength=width)
            stl.pack(fill="x", pady=(6, 0))
            self._wrap_labels.append(stl)
            r3 = tk.Frame(card, bg=C_CARD)
            r3.pack(fill="x", pady=(8, 0))
            ttk.Button(r3, text="⌕  Quét file", style="AccentSmall.TButton",
                       command=lambda i=i: self.scan_folder_files(i)).pack(side="left")
            ttk.Button(r3, text="✎  Sửa", style="Small.TButton",
                       command=lambda i=i: self.open_folder_dialog(i)).pack(side="left", padx=(6, 0))
            more = ttk.Menubutton(r3, text="⋯", style="Small.TButton", width=3)
            mm = tk.Menu(more, tearoff=0, font=(FONT, 10))
            mm.add_command(label="↗  Mở thư mục trên Fshare", command=lambda u=f.get("url", ""): (
                self._check_firefox() and open_in_firefox(self.cfg["firefox_path"], u)))
            mm.add_command(label="⚕  Chẩn đoán (xuất danh sách file + lý do bỏ qua)...",
                           command=lambda i=i: self.diagnose_folder(i))
            mm.add_separator()
            mm.add_command(label="▲  Đưa lên trên", command=lambda i=i: self.folder_move(i, -1))
            mm.add_command(label="▼  Đưa xuống dưới", command=lambda i=i: self.folder_move(i, 1))
            mm.add_separator()
            mm.add_command(label="✕  Xóa thư mục", command=lambda i=i: self.folder_delete(i))
            more["menu"] = mm
            more.pack(side="left", padx=(6, 0))
        if not self.cfg["folders"]:
            tk.Label(self.card_inner, text="Chưa có thư mục nào.\n\nBấm \"＋ Thêm thư mục\" hoặc \"⧉ Dán nhiều link\"\n"
                                           "để thêm các thư mục Fshare chứa phim.",
                     bg=C_BG, fg=C_MUTED, font=(FONT, 10), justify="center").pack(pady=40)
        tk.Frame(self.card_inner, bg=C_BG, height=8).pack()
        on = sum(1 for f in self.cfg["folders"] if f.get("enabled", True))
        rv = sum(1 for f in self.cfg["folders"] if f.get("reviewed"))
        self.folders_ver = getattr(self, "folders_ver", 0) + 1
        self.folder_count.set(f"{len(self.cfg['folders'])} thư mục · quét {on} · đã duyệt {rv}"
                              + (f" · hiện {shown}" if q else ""))
        self.update_stats()

    def _toggle_folder(self, i):
        self.cfg["folders"][i]["enabled"] = not self.cfg["folders"][i].get("enabled", True)
        self.save_config(); self.refresh_folders()

    def folder_check_all(self, on):
        for f in self.cfg["folders"]:
            f["enabled"] = bool(on)
        self.save_config(); self.refresh_folders()

    def _add_folder(self, name, url, include="", exclude="", note="", local_dir=""):
        code = get_code(url)
        if not code or any(get_code(f["url"]) == code for f in self.cfg["folders"]):
            return False
        self.cfg["folders"].append({"name": name.strip(), "url": f"https://www.fshare.vn/folder/{code}",
                                    "enabled": True, "include": include.strip(), "exclude": exclude.strip(),
                                    "note": note.strip(), "local_dir": local_dir.strip()})
        return True

    def folder_move(self, i, step):
        j = i + step
        fl = self.cfg["folders"]
        if 0 <= j < len(fl):
            fl[i], fl[j] = fl[j], fl[i]
            if self.active_folder in (i, j):
                self.active_folder = j if self.active_folder == i else i
                if self.fp:
                    self.fp["index"] = self.active_folder
            self.save_config(); self.refresh_folders()

    def folder_delete(self, i):
        f = self.cfg["folders"][i]
        if not messagebox.askyesno(APP_NAME, f"Xóa thư mục \"{f.get('name') or f.get('url')}\" khỏi danh sách?\n"
                                             "(Danh sách file đã có của thư mục vẫn được giữ.)"):
            return
        del self.cfg["folders"][i]
        if self.active_folder == i:
            self.active_folder, self.fp = None, None
            self._fp_clear()
        elif self.active_folder is not None and self.active_folder > i:
            self.active_folder -= 1
            if self.fp:
                self.fp["index"] = self.active_folder
        self.save_config(); self.refresh_folders()

    # ----- khung bên phải: file của 1 thư mục
    def _build_file_panel(self, p):
        self.fp_title = tk.StringVar(value="Danh sách file")
        self.fp_info = tk.StringVar(value="")
        hdr = ttk.Frame(p, style="Card.TFrame")
        hdr.pack(fill="x", padx=(12, 0))
        ttk.Label(hdr, textvariable=self.fp_title, style="Section.TLabel").pack(anchor="w")
        ttk.Label(hdr, textvariable=self.fp_info, style="Muted.TLabel", wraplength=640, justify="left").pack(
            anchor="w", pady=(2, 0))

        bar = tk.Frame(p, bg=C_SOFT, highlightbackground=C_SOFT_BORDER, highlightthickness=1)
        bar.pack(fill="x", pady=(10, 8), padx=(12, 0))
        b1 = tk.Frame(bar, bg=C_SOFT, padx=10, pady=6)
        b1.pack(fill="x")
        self.fp_btns = []
        for text, img, cmd in (("Chọn tất cả", self.img_on, lambda: self._fp_pick_visible(True)),
                               ("Bỏ chọn", self.img_off, lambda: self._fp_pick_visible(False))):
            bt = ttk.Button(b1, text=text, image=img, compound="left", command=cmd)
            bt.pack(side="left", padx=(0, 6))
            self.fp_btns.append(bt)
        bt = ttk.Button(b1, text="↧  Chọn từ đầu đến dòng đang bấm", command=self._fp_pick_until)
        bt.pack(side="left")
        self.fp_btns.append(bt)
        b2 = tk.Frame(bar, bg=C_SOFT, padx=10)
        b2.pack(fill="x", pady=(0, 6))
        tk.Label(b2, text="⌕ Tìm", bg=C_SOFT, fg=C_MUTED, font=(FONT, 9)).pack(side="left")
        self.fp_search = tk.StringVar()
        ttk.Entry(b2, textvariable=self.fp_search, width=24).pack(side="left", padx=(4, 12))
        self.fp_search.trace_add("write", lambda *a: self._fp_refresh())
        tk.Label(b2, text="Hiện", bg=C_SOFT, fg=C_MUTED, font=(FONT, 9)).pack(side="left")
        self.fp_show = tk.StringVar(value="Tất cả")
        sc = ttk.Combobox(b2, textvariable=self.fp_show, state="readonly", width=14,
                          values=("Tất cả", "Chưa có", "Đã có", "Đang chọn"))
        sc.pack(side="left", padx=4)
        sc.bind("<<ComboboxSelected>>", lambda e: self._fp_refresh())

        wrap, self.fp_tree = self._tree(p, ("name", "date", "size", "status"),
                                        ("TÊN FILE", "NGÀY UP", "DUNG LƯỢNG", "TRẠNG THÁI"),
                                        (360, 100, 100, 190), height=12, check=True, stretch=("name",))
        wrap.pack(fill="both", expand=True, padx=(12, 0))
        self.fp_tree.bind("<Button-1>", self._fp_click, add="+")
        self.fp_tree.bind("<space>", self._fp_toggle_sel)
        self.fp_tree.bind("<<TreeviewSelect>>", lambda e: self._show_full(self.fp_tree))
        self.fp_tree.heading("#0", image=self.img_off, anchor="center",
                             command=lambda: self._fp_pick_visible(
                                 not all(c in self.fp["picked"] for c in self.fp_tree.get_children())
                                 if self.fp else True))
        self.fp_empty = tk.Label(self.fp_tree, text="Bấm \"⌕ Quét file\" ở một thư mục bên trái\n"
                                                    "để xem tất cả file của thư mục đó.",
                                 bg="white", fg=C_MUTED, font=(FONT, 11), justify="center")
        self.fp_empty.place(relx=0.5, rely=0.4, anchor="center")

        bot = ttk.Frame(p, style="Card.TFrame")
        bot.pack(fill="x", pady=(10, 0), padx=(12, 0))
        self.fp_mark = ttk.Button(bot, text="☑  Thêm vào Danh sách đã có", style="Accent.TButton",
                                  command=lambda: self._fp_mark(True), state="disabled")
        self.fp_mark.pack(side="left")
        self.fp_unmark = ttk.Button(bot, text="↺  Bỏ khỏi Danh sách đã có", command=lambda: self._fp_mark(False),
                                    state="disabled")
        self.fp_unmark.pack(side="left", padx=8)
        self.fp_save = ttk.Button(bot, text="✓  Lưu", style="Success.TButton", command=self._fp_save_click,
                                  state="disabled")
        self.fp_save.pack(side="right")
        self.fp_count = tk.StringVar()
        ttk.Label(p, textvariable=self.fp_count, style="Muted.TLabel").pack(anchor="w", padx=(12, 0), pady=(6, 0))

    def _fp_clear(self):
        self.fp_tree.delete(*self.fp_tree.get_children())
        self.fp_title.set("Danh sách file")
        self.fp_info.set("")
        self.fp_count.set("")
        self.fp_empty.place(relx=0.5, rely=0.4, anchor="center")
        for b in (self.fp_mark, self.fp_unmark, self.fp_save):
            b.config(state="disabled")

    def _fp_changes(self):
        if not self.fp:
            return 0
        return sum(1 for r in self.fp["rows"].values() if r["owned"] != r["orig"])

    def scan_folder_files(self, i):
        """Nút 'Quét file' của 1 thư mục: đọc tất cả file -> hiện ở khung bên phải."""
        if requests is None:
            messagebox.showerror(APP_NAME, "Thiếu thư viện 'requests'.")
            return
        if self.fp and self._fp_changes():
            ans = messagebox.askyesnocancel(APP_NAME, f"Thư mục \"{self.fp['fname']}\" có {self._fp_changes()} "
                                                      "thay đổi chưa lưu.\nLưu trước khi chuyển thư mục?")
            if ans is None:
                return
            if ans:
                self._fp_save()
        f = self.cfg["folders"][i]
        code = get_code(f.get("url"))
        if not code:
            messagebox.showwarning(APP_NAME, "Link thư mục không hợp lệ.")
            return
        self.active_folder = i
        self.refresh_folders()
        self.fp = {"index": i, "code": code, "fname": f.get("name") or code, "rows": {}, "order": [],
                   "picked": set(), "loading": True}
        self.fp_tree.delete(*self.fp_tree.get_children())
        self.fp_empty.place_forget()
        self.fp_title.set(self.fp["fname"])
        self.fp_info.set("Đang quét tất cả file trên Fshare...")
        self.fp_count.set("")
        for b in (self.fp_mark, self.fp_unmark, self.fp_save):
            b.config(state="disabled")
        cfg = json.loads(json.dumps(self.cfg))
        token = object()
        self.fp["token"] = token

        def job():
            try:
                fs = FshareFolder()
                real, _ = fs.list_folder(code)
                files = list(fs.walk(code))
                self.note_listing(files)
                exts = {e.lower() if e.startswith(".") else "." + e.lower()
                        for e in cfg.get("extensions", "").split() if e.strip()}
                inc, exc = f.get("include", ""), f.get("exclude", "")
                out, skipped = [], {}
                for it in files:
                    name, fc = it.get("name") or "", it.get("linkcode")
                    if not fc or not name:
                        continue
                    if exts and os.path.splitext(name)[1].lower() not in exts:
                        e = os.path.splitext(name)[1].lower() or "(không đuôi)"
                        skipped[e] = skipped.get(e, 0) + 1
                        continue
                    if (inc or exc) and not match_keywords(name, inc, exc):
                        skipped["(lọc từ khóa)"] = skipped.get("(lọc từ khóa)", 0) + 1
                        continue
                    d = fs.item_date(it)
                    out.append((fc, name, it.get("size"), d))
                self.fp_skipped = skipped
                self.fp_errors = list(fs.errors)
                subdirs = list(fs.subdirs)
                self.q.put(("call", lambda: self._set_subs(code, subdirs), (), {}, threading.Event()))
                subs = ", ".join(f"{k or '(gốc)'}: {v}" for k, v in fs.subs.items())
                self.q.put(("log", f"[{f.get('name') or code}] Quét file: {len(files)} file · " + subs))
                for sub, err in fs.errors:
                    self.q.put(("log", f"[{f.get('name') or code}] ⚠ Thư mục con \"{sub}\" đọc lỗi: {err}"))
                self.q.put(("fp_loaded", token, i, real, out))
            except Exception as e:
                self.q.put(("fp_error", token, i, str(e)))
        threading.Thread(target=job, daemon=True).start()

    def _fp_loaded(self, token, i, real, files):
        if not self.fp or self.fp.get("token") is not token:
            return
        f = self.cfg["folders"][i]
        if not f.get("name") and real:
            f["name"] = real
            self.fp["fname"] = real
            self.fp_title.set(real)
        idx = self.owned_index()
        rows, seen = {}, set()
        for fc, name, size, d in files:
            if fc in seen:
                continue
            seen.add(fc)
            own = self.is_owned(idx, fc, self.fp["code"], name)
            h = self.history.get(fc, {})
            rows[fc] = {"name": name, "size": size, "date": d.strftime("%d/%m/%Y") if d else "",
                        "owned": own, "orig": own, "hist": h.get("status", ""), "sub": self.sub_of.get(fc, "")}
        order = sorted(rows, key=lambda c: (natural_key(rows[c]["sub"]), natural_key(rows[c]["name"])))
        self.fp.update(rows=rows, order=order, loading=False)
        for c in order:
            r = rows[c]
            self.fp_tree.insert("", "end", iid=c, text="", image=self.img_off,
                                values=(sub_label(r["sub"], r["name"]), r["date"], human_size(r["size"]), ""))
        n_own = sum(1 for r in rows.values() if r["owned"])
        n_sub = len({r["sub"] for r in rows.values() if r["sub"]})
        sk = getattr(self, "fp_skipped", {}) or {}
        self.fp_info.set(f"{len(order)} file" + (f" (trong {n_sub} thư mục con)" if n_sub else "")
                         + f"  ·  {n_own} đã có  ·  {len(order) - n_own} chưa có."
                         + (f"   Bỏ qua {sum(sk.values())} file: " + ", ".join(f"{k} ×{v}" for k, v in sk.items())
                            if sk else "   Tích chọn file → \"Thêm vào Danh sách đã có\" → \"Lưu\".")
                         + (f"   ⚠ {len(self.fp_errors)} thư mục con đọc lỗi (xem nhật ký)"
                            if getattr(self, "fp_errors", None) else ""))
        self._update_folder_stats(i, len(order), n_own, error="")
        self._fp_refresh()

    def _link_label(self, lbl, url, color):
        """Nhãn link: bấm mở trên Firefox, chuột phải copy link, rê chuột gạch chân."""
        if not url:
            return
        base = lbl.cget("font")
        lbl.bind("<Button-1>", lambda e: self._check_firefox() and open_in_firefox(self.cfg["firefox_path"], url))
        lbl.bind("<Button-3>", lambda e: self._copy_text(url, "Đã copy link: " + url))
        lbl.bind("<Enter>", lambda e: lbl.config(font=tkfont.Font(font=base, underline=True)))
        lbl.bind("<Leave>", lambda e: lbl.config(font=base))

    def _copy_text(self, text, msg=""):
        self.clipboard_clear()
        self.clipboard_append(text)
        if msg:
            self.status_var.set(msg)

    def _toggle_subs(self, code):
        (self._subs_open.discard if code in self._subs_open else self._subs_open.add)(code)
        self.refresh_folders()

    def _set_subs(self, code, subdirs):
        for f in self.cfg["folders"]:
            if get_code(f.get("url")) == code:
                if f.get("subs") != subdirs:
                    f["subs"] = subdirs
                    save_json(CONFIG_FILE, self.cfg)
                    self.refresh_folders()
                break

    def _update_folder_stats(self, i, total, owned, error=None, reviewed=None):
        f = self.cfg["folders"][i]
        f.update(total=total, owned=owned, last_scan=time.time())
        if error is not None:
            f["last_error"] = error
        if reviewed is not None:
            f["reviewed"] = reviewed
        self.save_config(); self.refresh_folders()

    def _fp_label(self, r):
        if r["owned"] and not r["orig"]:
            return "✔  Đã có (chưa lưu)", "done"
        if not r["owned"] and r["orig"]:
            return "↺  Sẽ bỏ khỏi DS (chưa lưu)", "err"
        if r["owned"]:
            return "✔  Đã có", "done"
        if r["hist"] in WATCH_STATUSES:
            return "⬇  Đang tải", "dl"
        return "●  Chưa có", "new"

    def _fp_visible(self, c):
        r = self.fp["rows"][c]
        qq = strip_accents(self.fp_search.get().strip())
        if qq and qq not in strip_accents(r["name"]):
            return False
        return {"Tất cả": True, "Chưa có": not r["owned"], "Đã có": r["owned"],
                "Đang chọn": c in self.fp["picked"]}.get(self.fp_show.get(), True)

    def _fp_refresh(self):
        if not self.fp or self.fp.get("loading"):
            return
        n = 0
        for c in self.fp["order"]:
            if not self.fp_tree.exists(c):
                continue
            if self._fp_visible(c):
                self.fp_tree.move(c, "", "end")
                text, tag = self._fp_label(self.fp["rows"][c])
                self.fp_tree.set(c, "status", text)
                self.fp_tree.item(c, image=self.img_on if c in self.fp["picked"] else self.img_off,
                                  tags=(["odd"] if n % 2 else []) + [tag])
                n += 1
            else:
                self.fp_tree.detach(c)
        rows = self.fp["rows"]
        own = sum(1 for r in rows.values() if r["owned"])
        ch = self._fp_changes()
        picked = len(self.fp["picked"])
        self.fp_count.set(f"Đang chọn {picked} file   ·   Đã có {own}/{len(rows)}"
                          + (f"   ·   {ch} thay đổi CHƯA LƯU" if ch else ""))
        kids = self.fp_tree.get_children()
        self.fp_tree.heading("#0", image=self.img_on if kids and all(k in self.fp["picked"] for k in kids)
                             else self.img_off)
        self.fp_mark.config(state="normal" if picked else "disabled",
                            text=f"☑  Thêm vào Danh sách đã có ({picked})" if picked else "☑  Thêm vào Danh sách đã có")
        self.fp_unmark.config(state="normal" if picked else "disabled")
        reviewed = self.cfg["folders"][self.fp["index"]].get("reviewed")
        self.fp_save.config(state="normal" if (ch or not reviewed) else "disabled")

    def _fp_pick_visible(self, on):
        if not self.fp:
            return
        for c in self.fp_tree.get_children():
            (self.fp["picked"].add if on else self.fp["picked"].discard)(c)
        self._fp_refresh()

    def _fp_pick_until(self):
        if not self.fp:
            return
        sel = self.fp_tree.selection()
        if not sel:
            messagebox.showinfo(APP_NAME, "Bấm vào 1 dòng (VD: tập cuối cùng bạn đã có) rồi bấm lại nút này.")
            return
        kids = list(self.fp_tree.get_children())
        last = max(kids.index(x) for x in sel if x in kids)
        for c in kids[:last + 1]:
            self.fp["picked"].add(c)
        self._fp_refresh()

    def _fp_click(self, e):
        if not self.fp:
            return None
        if self.fp_tree.identify_region(e.x, e.y) not in ("tree", "cell") or self.fp_tree.identify_column(e.x) != "#0":
            return None
        c = self.fp_tree.identify_row(e.y)
        if c:
            p = self.fp["picked"]
            p.discard(c) if c in p else p.add(c)
            self._fp_refresh()
        return "break"

    def _fp_toggle_sel(self, _e=None):
        if not self.fp:
            return "break"
        sel = self.fp_tree.selection()
        if sel:
            on = any(c not in self.fp["picked"] for c in sel)
            for c in sel:
                (self.fp["picked"].add if on else self.fp["picked"].discard)(c)
            self._fp_refresh()
        return "break"

    def _fp_mark(self, on):
        if not self.fp:
            return
        for c in self.fp["picked"]:
            self.fp["rows"][c]["owned"] = on
        self.fp["picked"].clear()
        self._fp_refresh()

    def _fp_save_click(self):
        n = self._fp_save()
        if n is not None:
            self.status_var.set(n)

    def _fp_save(self):
        if not self.fp or self.fp.get("loading"):
            return None
        fp = self.fp
        f = self.cfg["folders"][fp["index"]]
        add = rem = 0
        for c, r in fp["rows"].items():
            if r["owned"] and not r["orig"]:
                self.add_owned(c, fp["code"], fp["fname"], r["name"], r["size"], "manual")
                add += 1
            elif not r["owned"] and r["orig"]:
                self.remove_owned(c, fp["code"], r["name"])
                rem += 1
            r["orig"] = r["owned"]
        self.save_owned()
        own = sum(1 for r in fp["rows"].values() if r["owned"])
        self._update_folder_stats(fp["index"], len(fp["rows"]), own, reviewed=True)
        msg = (f"[{fp['fname']}] Đã lưu: +{add} file vào Danh sách đã có" + (f", bỏ {rem} file" if rem else "")
               + f". Thư mục đã duyệt: {own}/{len(fp['rows'])} file đã có.")
        self.log(msg)
        self.fp_info.set(f"{len(fp['rows'])} file  ·  {own} đã có  ·  {len(fp['rows']) - own} chưa có.   ✔ Đã lưu.")
        self._fp_refresh()
        return msg

    # ================================================================ TAB QUÉT & TẢI
    def _build_scan(self):
        cards = ttk.Frame(self.tab_scan, style="Card.TFrame")
        cards.pack(fill="x")
        self.stat_vars = {}
        for i, (key, title, color, glyph) in enumerate((("folders", "Thư mục sẽ quét", "#0f766e", "▦"),
                                                 ("new", "File mới chưa có", C_BLUE, "●"),
                                                 ("dl", "Đang tải", C_ORANGE, "⬇"),
                                                 ("owned", "Danh sách đã có", C_GREEN, "✔"))):
            card, var = self._stat_card(cards, title, color, glyph)
            card.grid(row=0, column=i, sticky="we", padx=(0 if i == 0 else 10, 0))
            cards.columnconfigure(i, weight=1)
            self.stat_vars[key] = var

        bar = ttk.Frame(self.tab_scan, style="Card.TFrame")
        bar.pack(fill="x", pady=(14, 10))
        ttk.Label(bar, text="Chỉ hiện những file CHƯA có trong Danh sách đã có (thư mục chưa duyệt sẽ không tự tải).",
                  style="Muted.TLabel").pack(side="left")
        self.btn_stop = ttk.Button(bar, text="■  Dừng", style="Danger.TButton", command=self.stop_flag.set,
                                   state="disabled")
        self.btn_go = ttk.Button(bar, text="⬇  Quét & Tải", style="Accent.TButton",
                                 command=lambda: self.start_scan(True))
        self.btn_scan = ttk.Button(bar, text="⌕  Quét", command=lambda: self.start_scan(False))
        self.btn_stop.pack(side="right")
        self.btn_go.pack(side="right", padx=8)
        self.btn_scan.pack(side="right")

        sel = tk.Frame(self.tab_scan, bg=C_SOFT, highlightbackground=C_SOFT_BORDER, highlightthickness=1)
        sel.pack(fill="x", pady=(0, 8))
        r1 = tk.Frame(sel, bg=C_SOFT, padx=10, pady=6)
        r1.pack(fill="x")
        ttk.Button(r1, text="Chọn tất cả", image=self.img_on, compound="left",
                   command=lambda: self.check_all(True)).pack(side="left")
        ttk.Button(r1, text="Bỏ chọn", image=self.img_off, compound="left",
                   command=lambda: self.check_all(False)).pack(side="left", padx=6)
        self.sel_var = tk.StringVar(value="Đã chọn: 0")
        tk.Label(r1, textvariable=self.sel_var, bg=C_SOFT, fg=C_ACCENT, font=(FONT, 10, "bold")
                 ).pack(side="left", padx=12)
        self.btn_dl_sel = ttk.Button(r1, text="⬇  Tải mục đã chọn", style="Accent.TButton",
                                     command=self.start_download_selected, state="disabled")
        self.btn_dl_sel.pack(side="right")
        self.btn_own_sel = ttk.Button(r1, text="☑  Đánh dấu đã có", command=self.mark_selected_owned,
                                      state="disabled")
        self.btn_own_sel.pack(side="right", padx=8)
        r2 = tk.Frame(sel, bg=C_SOFT, padx=10)
        r2.pack(fill="x", pady=(0, 6))
        tk.Label(r2, text="⌕ Tìm", bg=C_SOFT, fg=C_MUTED, font=(FONT, 9)).pack(side="left")
        self.scan_search_var = tk.StringVar()
        ttk.Entry(r2, textvariable=self.scan_search_var, width=26).pack(side="left", padx=(4, 12))
        self.scan_search_var.trace_add("write", lambda *a: self._apply_scan_filter())
        tk.Label(r2, text="Hiện", bg=C_SOFT, fg=C_MUTED, font=(FONT, 9)).pack(side="left")
        self.scan_show_var = tk.StringVar(value="Tất cả")
        sh = ttk.Combobox(r2, textvariable=self.scan_show_var, state="readonly", width=14,
                          values=("Tất cả", "Mới", "Đang tải", "Lỗi", "Chưa duyệt", "Đã chọn"))
        sh.pack(side="left", padx=4)
        sh.bind("<<ComboboxSelected>>", lambda e: self._apply_scan_filter())

        wrap, self.scan_tree = self._tree(self.tab_scan, ("folder", "note", "name", "date", "size", "status"),
                                          ("THƯ MỤC", "GHI CHÚ", "TÊN FILE", "NGÀY UP", "DUNG LƯỢNG", "TRẠNG THÁI"),
                                          (170, 170, 330, 100, 100, 220), height=10, check=True,
                                          stretch=("folder", "name", "note"))
        wrap.pack(fill="both", expand=True)
        self.checked = set()
        self.scan_tree.heading("#0", image=self.img_off, anchor="center", command=self._toggle_all_header)
        self.scan_tree.bind("<Button-1>", self._on_tree_click, add="+")
        self.scan_tree.bind("<space>", self._toggle_selected_rows)
        self.scan_tree.bind("<Double-1>", self._open_selected)
        self.scan_tree.bind("<<TreeviewSelect>>", lambda e: self._show_full(self.scan_tree))
        self.scan_items = {}
        self.scan_order = []
        self.scan_sort = (None, False)
        for c in ("folder", "note", "name", "date", "size", "status"):
            self.scan_tree.heading(c, command=lambda c=c: self._sort_scan(c))

        ttk.Label(self.tab_scan, text="Nhật ký   (bấm ô vuông để chọn, nhấp đúp để mở trên Firefox, "
                                      "bấm tiêu đề cột để sắp xếp)", style="Muted.TLabel").pack(anchor="w", pady=(10, 4))
        lb = tk.Frame(self.tab_scan, bg=C_BORDER, padx=1, pady=1)
        lb.pack(fill="x")
        self.log_box = tk.Text(lb, height=6, wrap="word", font=("Consolas", 9), bg="#101418", fg="#cbd5e1",
                               insertbackground="white", relief="flat", padx=10, pady=8)
        self.log_box.pack(fill="both")

    def _row_visible(self, iid):
        q = strip_accents(self.scan_search_var.get().strip())
        if q and q not in strip_accents(" ".join(str(v) for v in self.scan_tree.item(iid, "values"))):
            return False
        show = self.scan_show_var.get()
        tags = self.scan_tree.item(iid, "tags")
        return {"Tất cả": True, "Mới": "new" in tags, "Đang tải": "dl" in tags or "done" in tags,
                "Lỗi": "err" in tags, "Chưa duyệt": "skip" in tags,
                "Đã chọn": iid in self.checked}.get(show, True)

    def _set_row(self, iid, status, tag):
        if self.scan_tree.exists(iid):
            vals = list(self.scan_tree.item(iid, "values"))
            vals[5] = status
            tags = [t for t in self.scan_tree.item(iid, "tags") if t == "odd"] + [tag]
            self.scan_tree.item(iid, values=vals, tags=tags)

    def _update_sel_count(self):
        n = len(self._checked())
        total = len(self.scan_tree.get_children())
        self.sel_var.set(f"Đã chọn: {n}/{total}")
        vis = [i for i in self.scan_tree.get_children() if i in self.checked]
        self.scan_tree.heading("#0", image=self.img_on if total and len(vis) == total else self.img_off)
        busy = bool(self.worker and self.worker.is_alive())
        self.btn_dl_sel.config(state="normal" if n and not busy else "disabled",
                               text=f"⬇  Tải mục đã chọn ({n})" if n else "⬇  Tải mục đã chọn")
        self.btn_own_sel.config(state="normal" if n and not busy else "disabled")

    def _show_full(self, tree):
        """Bấm 1 dòng -> hiện đầy đủ nội dung dòng đó ở thanh trạng thái (khi cột bị hẹp)."""
        sel = tree.selection()
        if sel:
            vals = [str(v) for v in tree.item(sel[0], "values") if str(v).strip()]
            self.status_var.set("   ·   ".join(vals))

    def check_new_only(self):
        for i in self.scan_tree.get_children():
            self._set_check(i, "new" in self.scan_tree.item(i, "tags"))
        self._update_sel_count()

    def _open_selected(self, e=None):
        if e is not None and self.scan_tree.identify_column(e.x) == "#0":
            return
        sel = self.scan_tree.selection()
        if not sel or not self._check_firefox():
            return
        for code in sel:
            open_in_firefox(self.cfg["firefox_path"], f"https://www.fshare.vn/file/{code}")

    def mark_selected_owned(self):
        codes = self._checked()
        if not codes:
            return
        for c in codes:
            it = self.scan_items.get(c)
            if it:
                fcode, fname, name, size, folder_code = it
                self.add_owned(fcode, folder_code, fname, name, size, "manual")
                if self.scan_tree.exists(c):
                    self.scan_tree.delete(c)
                self.checked.discard(c)
        self.scan_order = [i for i in self.scan_order if self.scan_tree.exists(i)]
        self.save_owned()
        self._new_count = max(0, getattr(self, "_new_count", 0) - len(codes))
        self.rows_ver += 1
        self.log(f"Đã thêm {len(codes)} file vào Danh sách đã có (sẽ không tải).")
        self._apply_scan_filter()
        self.update_stats()

    def start_scan(self, download, auto=False):
        if self.worker and self.worker.is_alive():
            return False
        warn = (lambda m: self.log("[Tự quét] " + m)) if auto else (lambda m: messagebox.showwarning(APP_NAME, m))
        if requests is None:
            warn("Thiếu thư viện 'requests'. Chạy: pip install requests")
            return False
        self.save_config()
        if not self.cfg["folders"]:
            warn("Chưa có thư mục Fshare nào (tab Thư mục Fshare).")
            return False
        if not any(f.get("enabled", True) for f in self.cfg["folders"]):
            warn("Chưa chọn thư mục nào để quét (tab Thư mục Fshare).")
            return False
        if download and not os.path.isfile(self.cfg.get("firefox_path", "")):
            warn("Không tìm thấy firefox.exe. Chọn đường dẫn ở tab Cài đặt.")
            return False
        self.scan_tree.delete(*self.scan_tree.get_children())
        self.scan_items = {}
        self.scan_order = []
        self.checked = set()
        self._new_count = 0
        self.rows_ver += 1
        self.progress.config(value=0, maximum=max(1, sum(1 for f in self.cfg["folders"] if f.get("enabled", True))))
        self._run_worker(self._scan_job, download, auto)
        return True

    def _run_worker(self, target, *args):
        self.stop_flag.clear()
        for b in (self.btn_scan, self.btn_go, self.btn_dl_sel, self.btn_own_sel):
            b.config(state="disabled")
        self.btn_stop.config(state="normal")
        self.worker = threading.Thread(target=target, args=args, daemon=True)
        self.worker.start()

    def start_download_selected(self):
        if self.worker and self.worker.is_alive():
            return
        codes = self._checked()
        if not codes or not self._check_firefox():
            return
        items = [self.scan_items[c] for c in codes if c in self.scan_items]
        idx = self.owned_index()
        items = [it for it in items if not self.is_owned(idx, it[0], it[4], it[2])]
        if items:
            self._run_worker(self._download_job, items)

    def _download_job(self, items):
        try:
            self._download_items(json.loads(json.dumps(self.cfg)), items)
        except Exception as e:
            self.log(f"LỖI: {e}")
        finally:
            self.q.put(("done", "Đã dừng." if self.stop_flag.is_set() else "Xong. Tool tiếp tục theo dõi các tập đang tải..."))

    def diagnose_folder(self, i):
        """Quét 1 thư mục và ghi ra file .txt: mọi thư mục con, mọi file, và LÝ DO file bị bỏ qua."""
        f = self.cfg["folders"][i]
        code = get_code(f.get("url"))
        if not code:
            return
        fname = f.get("name") or code
        out_path = DATA_DIR / f"ChanDoan_{code}.txt"
        cfg = json.loads(json.dumps(self.cfg))
        owned = json.loads(json.dumps(self.owned))
        self.status_var.set(f"Đang chẩn đoán {fname}...")
        self.log(f"[{fname}] Chẩn đoán: đang đọc toàn bộ thư mục...")

        def job():
            lines = [f"{APP_TITLE} {APP_VERSION} - CHẨN ĐOÁN THƯ MỤC", f"Thời gian: {dt.datetime.now():%d/%m/%Y %H:%M:%S}",
                     f"Thư mục: {fname}  ({f.get('url')})",
                     f"Đuôi file cần tải: {cfg.get('extensions', '')}",
                     f"Từ khóa lọc: chỉ lấy=[{f.get('include', '')}]  loại=[{f.get('exclude', '')}]", ""]
            exts = {e.lower() if e.startswith(".") else "." + e.lower()
                    for e in cfg.get("extensions", "").split() if e.strip()}
            inc, exc = f.get("include", ""), f.get("exclude", "")
            by_name = {}
            for c, v in owned.items():
                by_name.setdefault(name_key(v.get("name", "")), []).append((c, v))
            fs = FshareFolder()
            stats = {"file": 0, "ok": 0, "skip": 0, "owned": 0}

            def visit(c, sub, depth, seen):
                if c in seen or depth > 12:
                    return
                seen.add(c)
                info = []
                try:
                    _, items = fs.list_folder(c, info)
                except Exception as e:
                    lines.append(f"[{sub or '(gốc)'}] mã={c}  ✘ LỖI ĐỌC: {e}")
                    return
                lines.append(f"[{sub or '(gốc)'}] mã={c}  {len(items)} mục")
                for pg, nb, nf, keys, meta, nxt in info:
                    lines.append(f"    trang {pg}: {nb} mục ({nf} mới)  meta={meta}  next={'có' if nxt else 'không'}")
                if items:
                    lines.append(f"    các trường: {', '.join(sorted(items[0].keys()))}")
                dirs = []
                for it in items:
                    nm = it.get("name") or ""
                    cc = fs.code_of(it)
                    if fs.is_dir(it):
                        lines.append(f"    [THƯ MỤC] {nm}  mã={cc or '(KHÔNG CÓ)'}  type={it.get('type')!r}")
                        if cc:
                            dirs.append((cc, (sub + " › " if sub else "") + nm))
                        continue
                    stats["file"] += 1
                    ext = os.path.splitext(nm)[1].lower()
                    if not cc:
                        why = "✘ BỎ QUA: không có mã file"
                    elif exts and ext not in exts:
                        why = f"✘ BỎ QUA: đuôi {ext or '(không đuôi)'} không có trong danh sách đuôi"
                    elif (inc or exc) and not match_keywords(nm, inc, exc):
                        why = "✘ BỎ QUA: bị lọc bởi từ khóa của thư mục"
                    elif cc in owned:
                        why = "● ĐÃ CÓ (đánh dấu theo mã file)"
                    else:
                        hits = [(oc, v) for oc, v in by_name.get(name_key(nm), []) if oc != cc]
                        why = "✔ CHƯA CÓ → sẽ hiện khi quét"
                        if hits:
                            oc, v = hits[0]
                            why = (f"? TRÙNG TÊN với file đã có: mã={oc} thư mục con=[{v.get('sub', '?')}] "
                                   "→ chỉ coi là đã có nếu mã đó không còn trên Fshare")
                    if why.startswith("✘"):
                        stats["skip"] += 1
                    elif why.startswith("●"):
                        stats["owned"] += 1
                    else:
                        stats["ok"] += 1
                    lines.append(f"    {nm}  |  mã={cc}  |  {human_size(it.get('size'))}  |  {why}")
                lines.append("")
                for cc, child in dirs:
                    visit(cc, child, depth + 1, seen)

            try:
                visit(code, "", 0, set())
                lines.insert(6, f"TỔNG: {stats['file']} file · chưa có {stats['ok']} · đã có {stats['owned']} · "
                                f"bị bỏ qua {stats['skip']}\n")
                with open(out_path, "w", encoding="utf-8-sig", newline="\r\n") as fh:
                    fh.write("\n".join(lines))
                self.q.put(("call", lambda: self._diagnose_done(fname, out_path, stats), (), {}, threading.Event()))
            except Exception as e:
                self.q.put(("log", f"[{fname}] Chẩn đoán lỗi: {e}"))
        threading.Thread(target=job, daemon=True).start()

    def _diagnose_done(self, fname, path, stats):
        self.log(f"[{fname}] Chẩn đoán xong: {stats['file']} file · chưa có {stats['ok']} · đã có {stats['owned']} · "
                 f"bỏ qua {stats['skip']}  →  {path}")
        self.status_var.set(f"Đã lưu kết quả chẩn đoán: {path.name}")
        try:
            if os.name == "nt":
                os.startfile(str(path))
        except OSError:
            pass

    def _scan_job(self, download, auto=False):
        cfg = json.loads(json.dumps(self.cfg))
        exts = {e.lower() if e.startswith(".") else "." + e.lower()
                for e in cfg.get("extensions", "").split() if e.strip()}
        history = json.loads(json.dumps(self.history))
        idx = self.owned_index()
        fs = FshareFolder(log=self.log)
        new_items, seen = [], set()
        stats = {"owned": 0, "dl": 0, "retry": 0, "err": 0, "unrev": 0, "failed": []}
        targets = [(i, f) for i, f in enumerate(cfg["folders"]) if f.get("enabled", True)]
        now_ts = time.time()
        self.log(f"=== {dt.datetime.now():%d/%m/%Y %H:%M} | Quét {len(targets)}/{len(cfg['folders'])} thư mục ===")
        try:
            for n, (orig_i, f) in enumerate(targets, 1):
                if self.stop_flag.is_set():
                    break
                code = get_code(f.get("url"))
                fname = f.get("name") or code
                self.q.put(("status", f"Đang quét {n}/{len(targets)}: {fname}"))
                if not code:
                    self.q.put(("folderstat", orig_i, {"last_scan": time.time(), "last_error": "Link không hợp lệ"}))
                    continue
                files, err = None, None
                for _ in range(3):
                    try:
                        if not f.get("name"):
                            real, _x = fs.list_folder(code)
                            if real:
                                fname = real
                                self.q.put(("foldername", orig_i, real))
                        files = list(fs.walk(code))
                        self.note_listing(files)
                        if fs.errors:
                            err = RuntimeError("; ".join(f"{a}: {b}" for a, b in fs.errors))
                            if _ < 2 and not self.stop_flag.wait(3):
                                continue        # thử lại cả thư mục, biết đâu Fshare hết bận
                            self.log(f"[{fname}] ⚠ {len(fs.errors)} thư mục con đọc lỗi: {err}")
                        break
                    except Exception as e:
                        err = e
                        if self.stop_flag.wait(3):
                            break
                if files is None:
                    self.log(f"[{fname}] LỖI sau 3 lần thử: {err}")
                    stats["failed"].append(fname)
                    self.q.put(("folderstat", orig_i, {"last_scan": time.time(), "last_error": str(err)[:120]}))
                    self.q.put(("progress", n, len(targets)))
                    continue
                reviewed = bool(f.get("reviewed"))
                idx = self.owned_index()
                inc, exc = f.get("include", ""), f.get("exclude", "")
                total = owned_n = new_n = 0
                rows, skipped = [], {}
                for it in files:
                    name, fcode = it.get("name") or "", it.get("linkcode")
                    if not fcode or not name or fcode in seen:
                        continue
                    if exts and os.path.splitext(name)[1].lower() not in exts:
                        e = os.path.splitext(name)[1].lower() or "(không đuôi)"
                        skipped[e] = skipped.get(e, 0) + 1
                        continue
                    if (inc or exc) and not match_keywords(name, inc, exc):
                        continue
                    seen.add(fcode)
                    total += 1
                    if self.is_owned(idx, fcode, code, name):
                        owned_n += 1
                        continue
                    new_n += 1
                    d = fs.item_date(it)
                    sub = it.get("_sub", "")
                    rows.append(((natural_key(sub), natural_key(name)), fcode, name, d, it.get("size"), sub))
                for _k, fcode, name, d, size, sub in sorted(rows, key=lambda r: r[0]):
                    row = (fname, one_line(f.get("note", "")), sub_label(sub, name), d.strftime("%d/%m/%Y") if d else "",
                           human_size(size))
                    item = (fcode, fname, name, size, code)
                    h = history.get(fcode, {})
                    st = h.get("status", "")
                    put = (lambda s_, t_: self.q.put(("row", fcode, row + (s_,), t_, item)))
                    if not reviewed:
                        stats["unrev"] += 1
                        put("◔  Thư mục chưa duyệt", "skip")
                    elif st in WATCH_STATUSES and now_ts - float(h.get("ts", now_ts) or now_ts) < STALE_HOURS * 3600:
                        stats["dl"] += 1
                        put("⬇  Đã mở, chờ tải xong", "dl")
                    elif st == STATUS_FAIL:
                        tries = int(h.get("tries", 1) or 1)
                        if tries < MAX_TRIES:
                            stats["retry"] += 1
                            put(f"↻  Thử lại (lỗi {tries}/{MAX_TRIES})", "new")
                            new_items.append(item)
                        else:
                            stats["err"] += 1
                            put(f"⚠  Lỗi {tries} lần - hãy kiểm tra", "err")
                    else:
                        put("●  Mới", "new")
                        new_items.append(item)
                self.q.put(("folderstat", orig_i, {"last_scan": time.time(), "last_error": "", "subs": list(fs.subdirs),
                                                   "total": total, "owned": owned_n}))
                n_sub = len({it.get("_sub") for it in files if it.get("_sub")})
                if skipped:
                    self.log(f"[{fname}] Bỏ qua {sum(skipped.values())} file khác đuôi: "
                             + ", ".join(f"{k} ×{v}" for k, v in skipped.items())
                             + "  (thêm đuôi trong Cài đặt → Tải về nếu cần)")
                self.log(f"[{fname}] {total} file" + (f" ({n_sub} thư mục con)" if n_sub else "")
                         + f" · đã có {owned_n} · chưa có {new_n}"
                         + ("" if reviewed else "  (chưa duyệt — vào tab Thư mục bấm Quét file)"))
                self.q.put(("progress", n, len(targets)))

            self.q.put(("newcount", len(new_items)))
            failed = stats["failed"]
            summary = (f"{dt.datetime.now():%H:%M %d/%m} · {len(targets)} thư mục · {len(new_items)} file mới"
                       + (f" · {len(failed)} thư mục lỗi" if failed else ""))
            self.q.put(("summary", summary))
            self.log(f"Tổng kết: {len(new_items)} file mới cần tải ({stats['retry']} thử lại), "
                     f"{stats['dl']} đang chờ tải xong, {stats['unrev']} file ở thư mục chưa duyệt.")
            if failed:
                self.log("Thư mục lỗi: " + ", ".join(failed))
                if auto:
                    self.q.put(("retry_soon", 600))
                self.q.put(("notify", f"{len(failed)} thư mục quét lỗi", ", ".join(failed[:3])))
            if download and new_items and not self.stop_flag.is_set():
                self._download_items(cfg, new_items, auto=auto)
            elif new_items and not download:
                self.q.put(("notify", f"Có {len(new_items)} file mới", self._names_preview([x[2] for x in new_items])))
        except Exception as e:
            self.log(f"LỖI: {e}")
        finally:
            self.q.put(("done", "Đã dừng." if self.stop_flag.is_set() else
                        ("Tự quét xong. " if auto else "Xong. ") + "Tool tiếp tục theo dõi các tập đang tải..."))

    def _warmup_firefox(self, cfg, clicker):
        """Firefox đang tắt: mở trước 1 tab trống, chờ tiện ích IDM nạp xong rồi mới mở link Fshare.
        (Firefox bản mới nạp tiện ích chậm hơn -> link mở ngay lúc khởi động sẽ bị Firefox tự tải.)"""
        wait = max(0, int(cfg.get("ff_warmup", 8) or 0))
        if not wait:
            return
        self.q.put(("status", f"Đang khởi động Firefox, chờ tiện ích IDM nạp ({wait}s)..."))
        open_in_firefox(cfg["firefox_path"], "about:blank")
        end = time.time() + 30
        while time.time() < end and not clicker.firefox_titles() and not self.stop_flag.is_set():
            time.sleep(0.5)
        self.stop_flag.wait(wait)

    def _download_items(self, cfg, items, auto=False):
        """Mở từng file trên Firefox -> IDM bấm Bắt đầu tải -> thêm vào Danh sách đã có -> đóng tab Fshare."""
        clicker = IdmClicker() if cfg.get("idm_autoclick", True) else None
        if clicker and not clicker.ok:
            clicker = None
        mode = cfg.get("close_firefox", CLOSE_FF_CHOICES[0])
        if mode not in CLOSE_FF_CHOICES:
            mode = CLOSE_FF_CHOICES[0]
        prev_fg = clicker.get_foreground() if clicker else None
        ff_was_running = bool(clicker.firefox_titles()) if clicker else True
        if clicker:
            clicker.click_once()
        self.q.put(("progress", 0, len(items)))
        ff_dirs = firefox_download_dirs()
        opened, started, ff_grabbed = 0, [], []
        for i, (fcode, fname, name, size, folder_code) in enumerate(items, 1):
            if self.stop_flag.is_set():
                break
            self.q.put(("status", f"Mở Firefox {i}/{len(items)}: {name}"))
            try:
                if clicker and not clicker.firefox_titles():
                    self._warmup_firefox(cfg, clicker)
                before = clicker.firefox_titles() if clicker else {}
                open_in_firefox(cfg["firefox_path"], f"https://www.fshare.vn/file/{fcode}")
                now = dt.datetime.now()
                rec = {"folder": fname, "folder_code": folder_code, "name": name, "size": size,
                       "ts": now.timestamp(), "sent_at": now.strftime("%d/%m/%Y %H:%M"), "status": STATUS_OPENED}
                self.q.put(("hist", fcode, dict(rec)))
                self.q.put(("rowstatus", fcode, STATUS_OPENED, "dl"))
                opened += 1
                if clicker:
                    self.q.put(("status", f"Chờ IDM {i}/{len(items)}: {name}"))
                    if clicker.wait_and_click(int(cfg.get("idm_timeout", 25)), self.stop_flag):
                        rec.update(status=STATUS_DL, tries=0)
                        self.q.put(("hist", fcode, dict(rec)))
                        self.q.put(("owned_add", fcode, folder_code, fname, name, size, "tool"))
                        self.q.put(("rowstatus", fcode, "⬇  Đang tải · đã thêm vào DS đã có", "done"))
                        started.append(name)
                        idm_mode = cfg.get("idm_window", IDM_WINDOW_CHOICES[0])
                        if idm_mode != "Giữ nguyên":
                            clicker.minimize_progress(hide=(idm_mode == IDM_WINDOW_CHOICES[0]))
                        if mode == "Chỉ đóng tab Fshare":
                            time.sleep(0.8)
                            clicker.close_fshare_tab(name, fcode, before)
                        elif mode == "Đóng hẳn Firefox":
                            clicker.close_firefox()
                        if auto:
                            clicker.focus(prev_fg)
                        else:
                            self.q.put(("focus",))
                    elif firefox_grabbed(ff_dirs, name, now.timestamp()):
                        # IDM không bắt link, Firefox tự tải -> KHÔNG thử lại (tránh tải trùng), vẫn theo dõi để xử lý tiếp
                        rec.update(status=STATUS_DL, tries=0, via="firefox")
                        self.q.put(("hist", fcode, dict(rec)))
                        self.q.put(("owned_add", fcode, folder_code, fname, name, size, "tool"))
                        self.q.put(("rowstatus", fcode, "⬇  Firefox tự tải (IDM không bắt) · đã thêm vào DS đã có", "done"))
                        ff_grabbed.append(name)
                        self.log(f"[{name}] ⚠ IDM không bắt link, Firefox tự tải vào thư mục Downloads. "
                                 "Tool vẫn theo dõi để copy/upload khi xong. Kiểm tra tiện ích IDM trong Firefox.")
                    else:
                        tries = int(self.history.get(fcode, {}).get("tries", 0) or 0) + 1
                        rec.update(status=STATUS_FAIL, tries=tries)
                        self.q.put(("hist", fcode, dict(rec)))
                        self.q.put(("rowstatus", fcode, f"⚠  IDM chưa nhận (lần {tries}/{MAX_TRIES})", "err"))
                        self.log(f"[{name}] Không thấy hộp thoại IDM (lần {tries}). Lần quét sau sẽ thử lại.")
            except Exception as e:
                self.q.put(("rowstatus", fcode, f"Lỗi: {e}", "err"))
                self.log(f"[{name}] LỖI: {e}")
            self.q.put(("progress", i, len(items)))
            if i < len(items):
                self.stop_flag.wait(int(cfg.get("delay", 4)))
        if clicker and started:
            if mode == "Chỉ đóng tab Fshare" and not ff_was_running:
                clicker.close_firefox()
            if auto:
                clicker.focus(prev_fg)
            else:
                self.q.put(("focus",))
        self.log(f"Đã mở {opened}/{len(items)} file trên Firefox." + ("" if clicker else " Qua IDM bấm Bắt đầu tải."))
        if ff_grabbed:
            self.q.put(("notify", f"IDM không bắt {len(ff_grabbed)} link",
                        "Firefox đã tự tải vào Downloads. Thử tăng \"Chờ Firefox khởi động\" trong Cài đặt."))
        if started:
            self.q.put(("notify", f"Đang tải {len(started)} file mới", self._names_preview(started)))
        elif opened and not clicker:
            self.q.put(("notify", f"Đã mở {opened} file trên Firefox", "Qua IDM bấm Bắt đầu tải."))
        return started

    # ================================================================ LỊCH SỬ / THEO DÕI
    def history_mark_done(self):
        sel = self.hist_tree.selection()
        for code in sel:
            h = self.history.get(code)
            if h:
                h["status"] = STATUS_DONE
                h["done_ts"] = time.time()
                self.add_owned(code, h.get("folder_code", ""), h.get("folder", ""), h.get("name", ""),
                               h.get("size"), "manual")
        if sel:
            self.save_owned(); self.save_history(); self.refresh_history()

    def _monitor_tick(self, manual=False):
        if not manual:
            self.after(8000, self._monitor_tick)
        if self.monitor_busy:
            return
        pending = {c: (h.get("name", ""), h.get("size"), h.get("folder", "")) for c, h in self.history.items()
                   if h.get("status") in WATCH_STATUSES + (STATUS_FAIL,)}
        if not pending:
            if manual:
                self.status_var.set("Không có tập nào đang tải.")
            return
        folders = [x.strip() for x in self.cfg.get("watch_folders", "").split(";") if x.strip()]
        if self.cfg.get("watch_ff_downloads", True):
            if not hasattr(self, "_ff_dirs"):
                self._ff_dirs = firefox_download_dirs()
            folders += [d for d in self._ff_dirs if d not in folders]
        organize = bool(self.cfg.get("organize"))
        fcfg = {f.get("name", ""): f for f in self.cfg["folders"]}
        cfg_copy = json.loads(json.dumps(self.cfg))
        self.monitor_busy = True

        def job():
            try:
                res = scan_completed(folders, pending)
                if res and organize:
                    for code, path in list(res.items()):
                        fname = pending[code][2] or "Khác"
                        dest = local_dir_for(cfg_copy, fcfg.get(fname), fname)
                        if not dest:
                            continue
                        try:
                            res[code] = copy_to_library(path, dest)
                            self.q.put(("log", f"→ Đã copy vào {os.path.dirname(res[code])}"))
                        except Exception as e:
                            self.q.put(("log", f"Không copy được {os.path.basename(path)}: {e}"))
                self.q.put(("completed", res, manual, len(pending)))
            except Exception as e:
                self.q.put(("log", f"Lỗi kiểm tra tải xong: {e}"))
            finally:
                self.monitor_busy = False
        threading.Thread(target=job, daemon=True).start()

    def log(self, msg):
        self.q.put(("log", msg))

    def _poll(self):
        hist_changed = owned_changed = False
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "call":
                    _, fn, args, holder, ev = msg
                    try:
                        holder["res"] = fn(*args)
                    except Exception as e:
                        holder["err"] = e
                    ev.set()
                    continue
                if kind in ("row", "rowstatus"):
                    self.rows_ver += 1
                if kind == "webfiles":
                    self.web_files[msg[1]] = msg[2]
                    if msg[2].get("real"):
                        try:
                            i, f = self._folder_by_code(msg[1])
                            if not f.get("name"):
                                f["name"] = msg[2]["real"]
                                self.save_config(); self.refresh_folders()
                        except ValueError:
                            pass
                    continue
                if kind == "log":
                    line = f"{dt.datetime.now():%H:%M:%S}  {msg[1]}"
                    self.log_lines.append(line)
                    self.log_box.insert("end", line + "\n"); self.log_box.see("end")
                    try:
                        with open(LOG_FILE, "a", encoding="utf-8") as fh:
                            fh.write(f"{dt.datetime.now():%Y-%m-%d} {line}\n")
                    except OSError:
                        pass
                elif kind == "status":
                    self.status_var.set(msg[1])
                elif kind == "progress":
                    self.progress.config(maximum=max(1, msg[2]), value=msg[1])
                elif kind == "row":
                    _, iid, values, tag = msg[:4]
                    self.scan_items[iid] = msg[4]
                    if not self.scan_tree.exists(iid):
                        n = len(self.scan_tree.get_children())
                        tags = [t for t in (("odd" if n % 2 else ""), tag) if t]
                        on = tag == "new"
                        self.scan_tree.insert("", "end", iid=iid, text="", values=values, tags=tags,
                                              image=self.img_on if on else self.img_off)
                        self.scan_order.append(iid)
                        if on:
                            self.checked.add(iid)
                        if not self._row_visible(iid):
                            self.scan_tree.detach(iid)
                        self._update_sel_count()
                elif kind == "rowstatus":
                    self._set_row(msg[1], msg[2], msg[3])
                elif kind == "newcount":
                    self._new_count = msg[1]
                    self.update_stats()
                elif kind == "hist":
                    self.history[msg[1]] = msg[2]
                    hist_changed = True
                elif kind == "owned_add":
                    _, fcode, folder_code, fname, name, size, src = msg
                    self.add_owned(fcode, folder_code, fname, name, size, src)
                    owned_changed = True
                elif kind == "folderstat":
                    i, data = msg[1], msg[2]
                    if i < len(self.cfg["folders"]):
                        self.cfg["folders"][i].update(data)
                        save_json(CONFIG_FILE, self.cfg)
                        self._folders_dirty = True
                elif kind == "summary":
                    self.last_summary = msg[1]
                    self.cfg["last_summary"] = msg[1]
                    self.side_last.config(text="Quét lần cuối:\n" + msg[1])
                elif kind == "retry_soon":
                    if self.cfg.get("auto_scan"):
                        self.next_auto_ts = min(self.next_auto_ts or 1e18, time.time() + msg[1])
                        self._update_auto_label()
                elif kind == "pkstatus":
                    self._set_pk_status(msg[1], msg[2])
                elif kind == "backup":
                    self._on_backup_done(msg[1], msg[2])
                elif kind == "upload":
                    self._on_upload_msg(msg[1], msg[2], msg[3])
                elif kind == "fp_loaded":
                    self._fp_loaded(msg[1], msg[2], msg[3], msg[4])
                elif kind == "fp_error":
                    if self.fp and self.fp.get("token") is msg[1]:
                        self.fp_info.set("⚠ Lỗi đọc thư mục: " + msg[3])
                        self._update_folder_stats(msg[2], self.cfg["folders"][msg[2]].get("total", 0),
                                                  self.cfg["folders"][msg[2]].get("owned", 0), error=msg[3][:120])
                elif kind == "completed":
                    res, manual, total = msg[1], msg[2], msg[3]
                    done_names = []
                    for code, path in res.items():
                        h = self.history.get(code)
                        if not h or h.get("status") == STATUS_DONE:
                            continue
                        h.update(status=STATUS_DONE, done_ts=time.time(), path=path)
                        if code not in self.owned:
                            self.add_owned(code, h.get("folder_code", ""), h.get("folder", ""), h.get("name", ""),
                                           h.get("size"), "tool")
                            owned_changed = True
                        self._set_row(code, "✔  Xong", "done")
                        self.q.put(("log", f"✔ Tải xong: {h.get('name', '')}"))
                        self.enqueue_upload(code)
                        done_names.append(h.get("name", ""))
                        hist_changed = True
                    if done_names:
                        self.notify(f"✔ Tải xong {len(done_names)} file", self._names_preview(done_names))
                        self.schedule_backup()
                    if manual:
                        self.status_var.set(f"Đã kiểm tra: {len(res)}/{total} tập tải xong.")
                elif kind == "foldername":
                    i, name = msg[1], msg[2]
                    if i < len(self.cfg["folders"]) and not self.cfg["folders"][i].get("name"):
                        self.cfg["folders"][i]["name"] = name
                        save_json(CONFIG_FILE, self.cfg)
                        self._folders_dirty = True
                elif kind == "focus":
                    if self.state() != "withdrawn":
                        self.bring_to_front()
                elif kind == "notify":
                    self.notify(msg[1], msg[2])
                elif kind == "tray":
                    what = msg[1]
                    if what == "show":
                        self.show_window()
                    elif what == "scan":
                        self.start_scan(True, auto=True)
                    elif what == "toggle_auto":
                        self.auto_var.set(not self.auto_var.get())
                        self._auto_changed()
                    elif what == "quit":
                        self.show_window()
                        self.quit_app()
                        return
                elif kind == "done":
                    self.status_var.set(msg[1])
                    for b in (self.btn_scan, self.btn_go):
                        b.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    self._update_sel_count()
                    if getattr(self, "_folders_dirty", False):
                        self._folders_dirty = False
                        self.refresh_folders()
                    self.update_stats()
        except queue.Empty:
            pass
        if hist_changed:
            self.save_history()
            self.refresh_history()
            self.hist_ver += 1
        if owned_changed:
            self.save_owned()
        self.after(150, self._poll)

    # ================================================================ GIAO DIỆN WEB
    def _open_web_local(self):
        import webbrowser
        if not getattr(self, "web_srv", None):
            messagebox.showwarning(APP_NAME, "Giao diện web đang tắt hoặc không mở được cổng.\n"
                                             "Vào Cài đặt -> Tự động & Web: bật giao diện web / đổi cổng khác (VD 8090) rồi Lưu.")
            return
        webbrowser.open(f"http://127.0.0.1:{int(self.cfg.get('web_port', 8080) or 8080)}")

    def open_firewall(self, quiet=False):
        """Cho iPhone / máy khác vào giao diện web: xóa luật CHẶN cũ (khi lỡ bấm Cancel ở hộp thoại 'Allow'
        của Windows) + thêm luật cho phép theo chương trình và theo cổng. Cần quyền Admin."""
        if os.name != "nt":
            return False
        import ctypes
        import tempfile
        port = int(self.cfg.get("web_port", 8080) or 8080)
        progs = {str(Path(sys.executable).resolve())}
        if not getattr(sys, "frozen", False):          # chạy bằng Python: cả python.exe và pythonw.exe
            d = Path(sys.executable).resolve().parent
            progs |= {str(d / "python.exe"), str(d / "pythonw.exe")}
        name = "Fshare Auto Download (web)"
        lines = ["$ErrorActionPreference = 'SilentlyContinue'"]
        for pg in sorted(progs):
            q = pg.replace("'", "''")
            lines.append(f"Get-NetFirewallApplicationFilter | Where-Object {{ $_.Program -eq '{q}' }} | "
                         "Get-NetFirewallRule | Where-Object { $_.Direction -eq 'Inbound' -and $_.Action -eq 'Block' } | "
                         "Remove-NetFirewallRule")
        lines.append(f"Remove-NetFirewallRule -DisplayName '{name}'")
        lines.append(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow -Protocol TCP "
                     f"-LocalPort {port} -Profile Any | Out-Null")
        for pg in sorted(progs):
            if os.path.isfile(pg):
                q = pg.replace("'", "''")
                lines.append(f"New-NetFirewallRule -DisplayName '{name}' -Direction Inbound -Action Allow "
                             f"-Program '{q}' -Profile Any | Out-Null")
        ps1 = Path(tempfile.gettempdir()) / "fad_firewall.ps1"
        ps1.write_text("\r\n".join(lines), encoding="utf-8-sig")
        r = ctypes.windll.shell32.ShellExecuteW(None, "runas", "powershell.exe",
                                                f'-NoProfile -ExecutionPolicy Bypass -File "{ps1}"', None, 0)
        if r <= 32:
            if not quiet:
                messagebox.showwarning(APP_NAME, "Chưa mở được (cần bấm Yes ở hộp thoại quyền Admin của Windows).")
            return False
        self.cfg["fw_ok_for"] = str(Path(sys.executable).resolve()) + f"|{port}"
        save_json(CONFIG_FILE, self.cfg)
        self.log(f"⛨ Đã cho phép giao diện web (cổng {port}) qua tường lửa Windows.")
        if not quiet:
            messagebox.showinfo(APP_NAME, "Đã cho phép. Mở lại trên iPhone (cùng Wi-Fi):\n" + "\n".join(self.web_urls()))
        return True

    def _first_run_firewall(self):
        """Giống hộp thoại 'Allow' của Windows lần đầu: hỏi 1 lần cho mỗi exe / cổng."""
        if os.name != "nt" or not self.cfg.get("web_on", True) or not getattr(self, "web_srv", None):
            return
        key = str(Path(sys.executable).resolve()) + f"|{int(self.cfg.get('web_port', 8080) or 8080)}"
        if self.cfg.get("fw_ok_for") == key or self.cfg.get("fw_asked") == key:
            return
        self.cfg["fw_asked"] = key
        save_json(CONFIG_FILE, self.cfg)
        if messagebox.askyesno(APP_NAME + " - Cho phép truy cập", "Cho phép iPhone / máy khác trong cùng Wi-Fi mở giao "
                               "diện web của tool?\n\nWindows sẽ hỏi quyền Admin -> bấm Yes.\n"
                               "(Làm lại sau: Cài đặt -> Tự động & Web -> \"Mở cổng tường lửa\")"):
            self.open_firewall()

    def call_ui(self, fn, *args, timeout=20):
        """Chạy fn trong luồng giao diện (Tk) và chờ kết quả - dùng cho các yêu cầu từ trang web."""
        if threading.current_thread() is threading.main_thread():
            return fn(*args)
        holder, ev = {}, threading.Event()
        self.q.put(("call", fn, args, holder, ev))
        if not ev.wait(timeout):
            raise RuntimeError("Tool đang bận, thử lại sau.")
        if "err" in holder:
            raise holder["err"]
        return holder.get("res")

    def web_urls(self):
        port = int(self.cfg.get("web_port", 8080) or 8080)
        ips = []
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ips.append(s.getsockname()[0])
            s.close()
        except OSError:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip not in ips and not ip.startswith("127."):
                    ips.append(ip)
        except OSError:
            pass
        return [f"http://{ip}:{port}" for ip in ips] or [f"http://localhost:{port}"]

    def start_web(self, _try=0):
        self.stop_web()
        if not self.cfg.get("web_on", True):
            self._web_label("Giao diện web: tắt")
            return
        port = int(self.cfg.get("web_port", 8080) or 8080)
        try:
            srv = WebServer(("0.0.0.0", port), make_web_handler(self))
        except OSError as e:
            if _try < 3:            # vừa khởi động lại / bản cũ chưa nhả cổng -> thử lại
                self.after(2500, lambda: self.start_web(_try + 1))
                return
            self.log(f"⚠ Không mở được giao diện web ở cổng {port}: {e} - cổng có thể đang bị chương trình khác "
                     "dùng, đổi cổng khác trong Cài đặt -> Tự động & Web.")
            self._web_label(f"⚠ Web lỗi cổng {port} (đổi cổng trong Cài đặt)")
            return
        self.web_srv = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        urls = self.web_urls()
        self.log("◍ Giao diện web: " + "  |  ".join(urls))
        self._web_label("◍ Web: " + urls[0].replace("http://", ""))

    def stop_web(self):
        srv = getattr(self, "web_srv", None)
        if srv:
            threading.Thread(target=lambda: (srv.shutdown(), srv.server_close()), daemon=True).start()
            self.web_srv = None

    def _web_label(self, text):
        try:
            self.side_web.config(text=text)
        except (AttributeError, tk.TclError):
            pass

    # ----- dữ liệu cho trang web (chạy trong luồng giao diện)
    def api_state(self):
        busy = bool(self.worker and self.worker.is_alive())
        on = [f for f in self.cfg["folders"] if f.get("enabled", True)]
        return {
            "app": APP_TITLE, "version": APP_VERSION, "busy": busy,
            "status": self.status_var.get(),
            "progress": [float(self.progress.cget("value") or 0), float(self.progress.cget("maximum") or 1)],
            "stats": {"folders": f"{len(on)}/{len(self.cfg['folders'])}", "new": getattr(self, "_new_count", 0),
                      "dl": sum(1 for h in self.history.values() if h.get("status") in WATCH_STATUSES),
                      "owned": len(self.owned),
                      "unreviewed": sum(1 for f in on if not f.get("reviewed"))},
            "auto": bool(self.cfg.get("auto_scan")), "interval": int(self.cfg.get("auto_interval", 60)),
            "next": (dt.datetime.fromtimestamp(self.next_auto_ts).strftime("%H:%M") if self.next_auto_ts else ""),
            "summary": self.last_summary or self.cfg.get("last_summary", ""),
            "log": list(self.log_lines)[-40:],
            "ver": {"rows": self.rows_ver, "hist": self.hist_ver, "folders": self.folders_ver},
        }

    def api_folders(self):
        out = []
        for i, f in enumerate(self.cfg["folders"]):
            text, color = self._folder_status(f)
            out.append({"i": i, "name": f.get("name", ""), "url": f.get("url", ""), "note": f.get("note", ""),
                        "enabled": f.get("enabled", True), "reviewed": bool(f.get("reviewed")),
                        "total": f.get("total", 0), "owned": f.get("owned", 0), "status": text, "color": color,
                        "include": f.get("include", ""), "exclude": f.get("exclude", ""),
                        "link": folder_link(get_code(f.get("url"))) or f.get("url", ""),
                        "subs": [{"path": d["path"], "url": folder_link(d["code"])} for d in f.get("subs", [])]})
        return out

    def _folder_by_code(self, code):
        for i, f in enumerate(self.cfg["folders"]):
            if get_code(f.get("url")) == code:
                return i, f
        raise ValueError("Không tìm thấy thư mục")

    def api_folder_add(self, d):
        if d.get("bulk"):
            added = 0
            for line in str(d["bulk"]).splitlines():
                line = line.strip()
                if not line:
                    continue
                name, url = ("", line)
                if "|" in line:
                    name, url = [x.strip() for x in line.split("|", 1)]
                added += self._add_folder(name, url)
        else:
            added = int(self._add_folder(d.get("name", ""), d.get("url", ""), note=d.get("note", "")))
            if not added:
                raise ValueError("Link không hợp lệ hoặc đã có trong danh sách.")
        self.save_config(); self.refresh_folders()
        return {"added": added}

    def api_folder_update(self, code, d):
        i, f = self._folder_by_code(code)
        if d.get("delete"):
            del self.cfg["folders"][i]
            if self.active_folder == i:
                self.active_folder, self.fp = None, None
                self._fp_clear()
            elif self.active_folder is not None and self.active_folder > i:
                self.active_folder -= 1
                if self.fp:
                    self.fp["index"] = self.active_folder
        else:
            for k in ("name", "note", "include", "exclude"):
                if k in d:
                    f[k] = str(d[k]).strip()
            if "enabled" in d:
                f["enabled"] = bool(d["enabled"])
        self.save_config(); self.refresh_folders()
        return {"ok": True}

    def api_scan_files(self, code):
        """Bắt đầu đọc tất cả file của 1 thư mục (chạy nền)."""
        i, f = self._folder_by_code(code)
        self.web_files[code] = {"status": "loading", "rows": [], "error": ""}
        cfg = json.loads(json.dumps(self.cfg))
        f = dict(f)

        def job():
            try:
                fs = FshareFolder()
                real, _ = fs.list_folder(code)
                files = list(fs.walk(code))
                self.note_listing(files)
                exts = {e.lower() if e.startswith(".") else "." + e.lower()
                        for e in cfg.get("extensions", "").split() if e.strip()}
                inc, exc = f.get("include", ""), f.get("exclude", "")
                rows, seen = [], set()
                for it in files:
                    name, fc = it.get("name") or "", it.get("linkcode")
                    if not fc or not name or fc in seen:
                        continue
                    if exts and os.path.splitext(name)[1].lower() not in exts:
                        continue
                    if (inc or exc) and not match_keywords(name, inc, exc):
                        continue
                    seen.add(fc)
                    d = fs.item_date(it)
                    rows.append({"code": fc, "name": name, "size": it.get("size"), "sub": it.get("_sub", ""),
                                 "date": d.strftime("%d/%m/%Y") if d else ""})
                rows.sort(key=lambda r: (natural_key(r["sub"]), natural_key(r["name"])))
                subdirs = list(fs.subdirs)
                self.q.put(("call", lambda: self._set_subs(code, subdirs), (), {}, threading.Event()))
                self.q.put(("webfiles", code, {"status": "ok", "rows": rows, "real": real, "error": ""}))
            except Exception as e:
                self.q.put(("webfiles", code, {"status": "error", "rows": [], "error": str(e)}))
        threading.Thread(target=job, daemon=True).start()
        return {"ok": True}

    def api_files(self, code):
        i, f = self._folder_by_code(code)
        st = self.web_files.get(code) or {"status": "none", "rows": []}
        idx = self.owned_index()
        rows = [dict(r, owned=self.is_owned(idx, r["code"], code, r["name"]), sizeh=human_size(r["size"]),
                     name=sub_label(r.get("sub", ""), r["name"]))
                for r in st.get("rows", [])]
        return {"status": st["status"], "error": st.get("error", ""), "name": f.get("name") or code,
                "reviewed": bool(f.get("reviewed")), "rows": rows}

    def api_files_save(self, code, add, remove):
        i, f = self._folder_by_code(code)
        st = self.web_files.get(code) or {}
        meta = {r["code"]: r for r in st.get("rows", [])}
        fname = f.get("name") or code
        for c in add:
            r = meta.get(c)
            if r:
                self.add_owned(c, code, fname, r["name"], r["size"], "manual")
        for c in remove:
            r = meta.get(c)
            if r:
                self.remove_owned(c, code, r["name"])
        self.save_owned()
        idx = self.owned_index()
        own = sum(1 for r in meta.values() if self.is_owned(idx, r["code"], code, r["name"]))
        f.update(total=len(meta), owned=own, last_scan=time.time(), reviewed=True, last_error="")
        if self.fp and self.fp.get("code") == code and not self.fp.get("loading"):   # đồng bộ khung trên máy
            for c, r in self.fp["rows"].items():
                if c in add:
                    r["owned"] = r["orig"] = True
                elif c in remove:
                    r["owned"] = r["orig"] = False
            self._fp_refresh()
        self.save_config(); self.refresh_folders()
        self.log(f"[{fname}] (web) Lưu Danh sách đã có: +{len(add)}" + (f", bỏ {len(remove)}" if remove else ""))
        return {"owned": own, "total": len(meta)}

    def api_scan(self, download):
        if self.worker and self.worker.is_alive():
            raise ValueError("Tool đang quét / tải, đợi xong đã.")
        if not self.start_scan(bool(download), auto=True):
            raise ValueError("Không quét được - xem nhật ký (chưa có thư mục / thiếu Firefox?).")
        return {"ok": True}

    def api_stop(self):
        self.stop_flag.set()
        return {"ok": True}

    def api_rows(self):
        out = []
        for c in self.scan_order:
            if not self.scan_tree.exists(c):
                continue
            v = self.scan_tree.item(c, "values")
            tags = [t for t in self.scan_tree.item(c, "tags") if t != "odd"]
            out.append({"code": c, "folder": v[0], "note": v[1], "name": v[2], "date": v[3], "size": v[4],
                        "status": v[5], "tag": tags[0] if tags else "", "checked": c in self.checked})
        return out

    def api_download(self, codes):
        if self.worker and self.worker.is_alive():
            raise ValueError("Tool đang quét / tải, đợi xong đã.")
        if not os.path.isfile(self.cfg.get("firefox_path", "")):
            raise ValueError("Không tìm thấy firefox.exe (xem tab Cài đặt trên máy tính).")
        idx = self.owned_index()
        items = [self.scan_items[c] for c in codes if c in self.scan_items]
        items = [it for it in items if not self.is_owned(idx, it[0], it[4], it[2])]
        if not items:
            raise ValueError("Không có file nào cần tải.")
        for c in self.scan_order:
            if self.scan_tree.exists(c):
                self._set_check(c, c in codes)
        self._update_sel_count()
        self._run_worker(self._download_job, items)
        return {"count": len(items)}

    def api_mark(self, codes):
        for c in self.scan_order:
            if self.scan_tree.exists(c):
                self._set_check(c, c in codes)
        self.mark_selected_owned()
        return {"ok": True}

    def api_history(self):
        items = sorted(self.history.items(), key=lambda kv: kv[1].get("ts", 0) or 0, reverse=True)[:300]
        return [{"code": c, "folder": h.get("folder", ""), "name": h.get("name", ""), "time": h.get("sent_at", ""),
                 "status": self._status_text(h.get("status", "")), "tag": self._status_tag(h.get("status", "")),
                 "pikpak": self._upload_text(h), "upload": h.get("upload", ""),
                 "can_upload": bool(h.get("path")) and h.get("upload") not in ("pending", "uploading")}
                for c, h in items]

    def api_history_upload(self, codes):
        n = sum(1 for c in codes if self.enqueue_upload(c, force=True))
        self.save_history(); self.refresh_history()
        return {"count": n}

    def api_auto(self, on, interval):
        self.auto_var.set(bool(on))
        if interval:
            self.interval_var.set(str(int(interval)))
        self._auto_changed()
        return {"ok": True}

    # ================================================================ PHẦN DÙNG LẠI
    def _set_icon(self):
        self.icon_big = self.icon_small = None
        try:
            self.icon_big = tk.PhotoImage(data=ICON64)
            self.icon_small = tk.PhotoImage(data=ICON28)
            self.iconphoto(True, self.icon_big)
        except tk.TclError:
            pass
        ico = BASE / "icon.ico"
        if getattr(sys, "frozen", False):
            ico = Path(getattr(sys, "_MEIPASS", BASE)) / "icon.ico"
        if os.name == "nt" and ico.is_file():
            try:
                self.iconbitmap(default=str(ico))
            except tk.TclError:
                pass

    def _install_edit_bindings(self):
        for cls in ("TEntry", "Entry", "Text", "TCombobox", "TSpinbox"):
            self.bind_class(cls, "<<Paste>>", self._paste)
            self.bind_class(cls, "<Button-3>", self._context_menu, add="+")
            self.bind_class(cls, "<Control-KeyPress>", self._ctrl_keys, add="+")
        self._menu = tk.Menu(self, tearoff=0, font=(FONT, 10))
        self._menu_target = None
        for label, ev in (("Cắt", "<<Cut>>"), ("Sao chép", "<<Copy>>"), ("Dán", "<<Paste>>")):
            self._menu.add_command(label=label, command=lambda e=ev: self._menu_target and
                                   self._menu_target.event_generate(e))
        self._menu.add_separator()
        self._menu.add_command(label="Chọn tất cả", command=lambda: self._select_all(self._menu_target))

    def _readonly(self, w):
        try:
            return str(w.cget("state")) in ("readonly", "disabled")
        except tk.TclError:
            return False

    def _paste(self, e):
        w = e.widget
        if self._readonly(w):
            return "break"
        txt = None
        if os.name == "nt":
            try:
                txt = read_clipboard_windows()
            except Exception:
                txt = None
        if txt is None:
            try:
                txt = self.clipboard_get()
            except tk.TclError:
                return "break"
        txt = clean_paste(txt)
        if w.winfo_class() == "Text":
            try:
                w.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            w.insert("insert", txt)
            w.see("insert")
        else:
            txt = one_line(txt) if "\n" in txt else txt   # ô 1 dòng: nối các dòng lại
            try:
                w.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            w.insert("insert", txt)
        return "break"

    def _ctrl_keys(self, e):
        # Ctrl+V/C/X/A theo phím vật lý -> dùng được cả khi bật bộ gõ / bàn phím tiếng Việt
        if e.keysym.lower() in ("v", "c", "x", "a"):
            if e.keysym.lower() == "a":
                return self._select_all(e.widget)
            return None
        ev = {86: "<<Paste>>", 67: "<<Copy>>", 88: "<<Cut>>"}.get(e.keycode)
        if ev:
            e.widget.event_generate(ev)
            return "break"
        if e.keycode == 65:
            return self._select_all(e.widget)
        return None

    def _select_all(self, w):
        if w is None:
            return "break"
        try:
            if w.winfo_class() == "Text":
                w.tag_add("sel", "1.0", "end-1c")
            else:
                w.select_range(0, "end")
                w.icursor("end")
        except tk.TclError:
            pass
        return "break"

    def _context_menu(self, e):
        self._menu_target = e.widget
        e.widget.focus_set()
        ro = self._readonly(e.widget)
        for i in (0, 2):  # Cắt, Dán
            self._menu.entryconfig(i, state="disabled" if ro else "normal")
        self._menu.tk_popup(e.x_root, e.y_root)
        return "break"

    def _make_check_images(self):
        def box(checked):
            n = 18
            img = tk.PhotoImage(width=n, height=n)
            border = C_ACCENT if checked else "#9ca3af"
            fill = C_ACCENT if checked else "#ffffff"
            for y in range(n):
                row = []
                for x in range(n):
                    corner = (x in (0, n - 1)) and (y in (0, n - 1))
                    if corner:
                        row.append("#ffffff")
                    elif x in (0, n - 1) or y in (0, n - 1):
                        row.append(border)
                    else:
                        row.append(fill)
                img.put("{" + " ".join(row) + "}", to=(0, y))
            if checked:  # dấu tích trắng
                pts = [(4, 9), (5, 10), (6, 11), (7, 12), (8, 11), (9, 10), (10, 9), (11, 8), (12, 7), (13, 6)]
                for x, y in pts:
                    for dx, dy in ((0, 0), (0, -1), (1, 0)):
                        img.put("#ffffff", to=(x + dx, y + dy))
            return img
        self.img_on, self.img_off = box(True), box(False)

    PAGE_INFO = {
        "folders": ("Thư mục Fshare", "Thêm thư mục phim, quét toàn bộ file (cả thư mục con) và đánh dấu tập đã có"),
        "scan": ("Quét & Tải", "Tìm tập mới chưa có trong Danh sách đã có và gửi sang IDM"),
        "history": ("Lịch sử tải", "Theo dõi tải xong · copy vào thư mục phim · upload PikPak"),
        "settings": ("Cài đặt", "Tải về · Lưu trữ & PikPak · Tự động & Web · Máy này & Sao lưu"),
    }

    def _build_header(self, parent):
        """Thanh tiêu đề trang (trắng, gọn) phía trên nội dung."""
        top = tk.Frame(parent, bg=C_CARD)
        top.pack(fill="x", side="top")
        tk.Frame(parent, bg=C_BORDER, height=1).pack(fill="x", side="top")
        inner = tk.Frame(top, bg=C_CARD, padx=22, pady=12)
        inner.pack(fill="x")
        left = tk.Frame(inner, bg=C_CARD)
        left.pack(side="left", fill="x", expand=True)
        self.top_title = tk.Label(left, text="", bg=C_CARD, fg=C_TEXT, font=(FONT, 16, "bold"), anchor="w")
        self.top_title.pack(anchor="w")
        self.top_sub = tk.Label(left, text="", bg=C_CARD, fg=C_MUTED, font=(FONT, 9), anchor="w")
        self.top_sub.pack(anchor="w")
        self.top_auto = tk.Label(inner, text="", bg=C_SOFT, fg=C_ACCENT_HOVER, font=(FONT, 10, "bold"),
                                 padx=14, pady=6, cursor="hand2")
        self.top_auto.pack(side="right")
        self.top_upd = tk.Label(inner, text="⟳  Cập nhật", bg=C_CARD, fg=C_MUTED, font=(FONT, 10, "bold"),
                                padx=12, pady=6, cursor="hand2", highlightbackground=C_BORDER, highlightthickness=1)
        self.top_upd.pack(side="right", padx=(0, 10))
        self.top_upd.bind("<Button-1>", lambda e: self.check_update(silent=False))
        self.top_auto.bind("<Button-1>", lambda e: (self.select_page("settings"), self._settings_page("auto")))
        self.header_auto_text = ""

    def select_page(self, key):
        self.page = key
        self.nb.select(self.nav[key]["frame"])
        t, sub = self.PAGE_INFO.get(key, ("", ""))
        try:
            self.top_title.config(text=t)
            self.top_sub.config(text=sub)
        except AttributeError:
            pass
        for k, n in self.nav.items():
            on = k == key
            bg = C_SIDE_HOVER if on else C_SIDE
            for w in ("row", "icon", "label"):
                n[w].config(bg=bg)
            n["bar"].config(bg=C_ACCENT if on else bg)
            n["icon"].config(fg=C_SIDE_ACTIVE if on else C_SIDE_MUTED)
            n["label"].config(fg="white" if on else C_SIDE_TEXT)

    def _nav_hover(self, key, inside):
        if getattr(self, "page", None) == key:
            return
        bg = C_SIDE_HOVER if inside else C_SIDE
        n = self.nav[key]
        for w in ("row", "icon", "label", "bar"):
            n[w].config(bg=bg)

    def set_badge(self, key, value, color=None):
        b = self.nav[key]["badge"]
        if value:
            b.config(text=str(value), bg=color or C_ACCENT)
            if not getattr(self, "side_collapsed", False):
                b.pack(side="right", padx=10)
        else:
            b.config(text="")
            b.pack_forget()

    def _build_statusbar(self, parent):
        bar = ttk.Frame(parent, style="Bg.TFrame", padding=(18, 6, 18, 8))
        bar.pack(fill="x", side="bottom")
        self.progress = ttk.Progressbar(bar, style="Pro.Horizontal.TProgressbar", mode="determinate", length=220)
        self.progress.pack(side="right")
        self.status_var = tk.StringVar(value="Sẵn sàng.")
        ttk.Label(bar, textvariable=self.status_var, style="Status.TLabel").pack(side="left")

    def _stat_card(self, parent, title, color, glyph=""):
        card = tk.Frame(parent, bg=C_CARD, highlightbackground=C_BORDER, highlightthickness=1)
        tk.Frame(card, bg=color, height=3).pack(side="top", fill="x")
        body = tk.Frame(card, bg=C_CARD, padx=16, pady=10)
        body.pack(fill="both", expand=True)
        var = tk.StringVar(value="0")
        txt = tk.Frame(body, bg=C_CARD)
        txt.pack(side="left", fill="both", expand=True)
        tk.Label(txt, text=title.upper(), bg=C_CARD, fg=C_MUTED, font=(FONT, 8, "bold")).pack(anchor="w")
        tk.Label(txt, textvariable=var, bg=C_CARD, fg=C_TEXT, font=(FONT, 20, "bold")).pack(anchor="w")
        if glyph:
            tk.Label(body, text=glyph, bg=C_CARD, fg=color, font=(FONT, 20)).pack(side="right", padx=(6, 0))
        return card, var

    def _apply_scan_filter(self):
        n = 0
        for iid in self.scan_order:
            if not self.scan_tree.exists(iid):
                continue
            if self._row_visible(iid):
                self.scan_tree.move(iid, "", "end")
                tags = [t for t in self.scan_tree.item(iid, "tags") if t != "odd"] + (["odd"] if n % 2 else [])
                self.scan_tree.item(iid, tags=tags)
                n += 1
            else:
                self.scan_tree.detach(iid)
        self._update_sel_count()

    def _sort_scan(self, col):
        cur, rev = self.scan_sort
        rev = (not rev) if cur == col else False
        self.scan_sort = (col, rev)
        idx = ("folder", "note", "name", "date", "size", "status").index(col)

        def key(iid):
            v = self.scan_tree.item(iid, "values")[idx] if self.scan_tree.exists(iid) else ""
            if col == "date":
                try:
                    return dt.datetime.strptime(v, "%d/%m/%Y")
                except ValueError:
                    return dt.datetime.min
            if col == "size":
                try:
                    return int(self.scan_items.get(iid, (0, 0, 0, 0))[3] or 0)
                except (TypeError, ValueError):
                    return 0
            return strip_accents(v)
        self.scan_order.sort(key=key, reverse=rev)
        heads = {"folder": "THƯ MỤC", "note": "GHI CHÚ", "name": "TÊN FILE", "date": "NGÀY UP",
                 "size": "DUNG LƯỢNG", "status": "TRẠNG THÁI"}
        for c, t in heads.items():
            self.scan_tree.heading(c, text=t + ((" ▼" if rev else " ▲") if c == col else ""))
        self._apply_scan_filter()

    def _checked(self):
        return [i for i in self.scan_order if i in self.checked and self.scan_tree.exists(i)]

    def _set_check(self, iid, on):
        (self.checked.add if on else self.checked.discard)(iid)
        self.scan_tree.item(iid, image=self.img_on if on else self.img_off)

    def check_all(self, on):
        for i in self.scan_tree.get_children():
            self._set_check(i, on)
        self._update_sel_count()

    def _toggle_all_header(self):
        kids = self.scan_tree.get_children()
        self.check_all(not (kids and all(i in self.checked for i in kids)))

    def _on_tree_click(self, e):
        if self.scan_tree.identify_region(e.x, e.y) not in ("tree", "cell") or self.scan_tree.identify_column(e.x) != "#0":
            return None
        iid = self.scan_tree.identify_row(e.y)
        if iid:
            self._set_check(iid, iid not in self.checked)
            self._update_sel_count()
        return "break"

    def _toggle_selected_rows(self, _e=None):
        sel = self.scan_tree.selection()
        if sel:
            on = any(i not in self.checked for i in sel)
            for i in sel:
                self._set_check(i, on)
            self._update_sel_count()
        return "break"

    def open_folder_dialog(self, index=None):
        """Hộp thoại thêm / sửa 1 thư mục Fshare."""
        old = dict(self.cfg["folders"][index]) if index is not None else {}
        win = tk.Toplevel(self)
        win.title("Sửa thư mục" if old else "Thêm thư mục Fshare")
        win.configure(bg=C_CARD)
        win.transient(self)
        win.resizable(True, False)
        try:
            win.iconphoto(False, self.icon_big)
        except (tk.TclError, TypeError):
            pass
        body = ttk.Frame(win, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        ttk.Label(body, text="Sửa thư mục" if old else "Thêm thư mục Fshare", style="Section.TLabel").grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        v = {k: tk.StringVar(value=old.get(k, "")) for k in ("name", "url", "include", "exclude", "local_dir",
                                                              "pikpak_dir")}
        v["local_dir"].set(from_portable(old.get("local_dir", "")))
        en = tk.BooleanVar(value=old.get("enabled", True))
        r = 1

        def field(label, key, hint=None, browse=False):
            nonlocal r
            ttk.Label(body, text=label).grid(row=r, column=0, sticky="w", pady=5, padx=(0, 12))
            e = ttk.Entry(body, textvariable=v[key], width=60)
            e.grid(row=r, column=1, sticky="we", pady=5)
            if browse:
                ttk.Button(body, text="Chọn...", command=lambda: (
                    lambda p: p and v[key].set(os.path.normpath(p)))(
                    filedialog.askdirectory(parent=win, title="Chọn thư mục chứa phim này"))).grid(
                    row=r, column=2, padx=(8, 0))
            r += 1
            if hint:
                ttk.Label(body, text=hint, style="Muted.TLabel").grid(row=r, column=1, columnspan=2, sticky="w")
                r += 1
            return e

        first = field("Link thư mục Fshare *", "url", "VD: https://www.fshare.vn/folder/ABCD1234")
        field("Tên phim", "name", "Để trống = tự lấy tên thư mục trên Fshare")
        ttk.Label(body, text="Ghi chú").grid(row=r, column=0, sticky="nw", pady=5, padx=(0, 12))
        nf = tk.Frame(body, bg=C_BORDER, padx=1, pady=1)
        nf.grid(row=r, column=1, columnspan=2, sticky="we", pady=5)
        note = tk.Text(nf, height=3, wrap="word", relief="flat", font=(FONT, 10), padx=6, pady=4,
                       undo=True, highlightthickness=0)
        note.pack(fill="x")
        note.insert("1.0", old.get("note", ""))
        r += 1
        field("Chỉ lấy file có", "include")
        field("Bỏ qua file có", "exclude", "Nhiều từ cách nhau dấu phẩy, không phân biệt dấu / hoa thường. "
                                          "VD: lấy \"lồng tiếng\", bỏ \"720p, vietsub\"")
        field("Thư mục phim trên máy", "local_dir", "Để trống = <Thư mục phim ở Cài đặt>\\<Tên phim>", browse=True)
        field("Thư mục con trên PikPak", "pikpak_dir", "Để trống = dùng tên phim (nằm trong thư mục gốc PikPak ở Cài đặt)")
        ttk.Checkbutton(body, text="Quét thư mục này", variable=en).grid(row=r, column=1, sticky="w", pady=(6, 0))
        r += 1

        def save(_e=None):
            code = get_code(v["url"].get())
            if not code:
                messagebox.showwarning(APP_NAME, "Link thư mục Fshare không hợp lệ.", parent=win)
                return
            for j, f in enumerate(self.cfg["folders"]):
                if j != index and get_code(f.get("url")) == code:
                    messagebox.showwarning(APP_NAME, f"Thư mục này đã có trong danh sách (dòng {j + 1}).", parent=win)
                    return
            rec = dict(old) if old and get_code(old.get("url")) == code else {}   # giữ thống kê nếu cùng link
            rec.update({"name": v["name"].get().strip(), "url": f"https://www.fshare.vn/folder/{code}",
                        "note": note.get("1.0", "end-1c").strip(), "include": v["include"].get().strip(),
                        "exclude": v["exclude"].get().strip(), "local_dir": to_portable(v["local_dir"].get().strip()),
                        "pikpak_dir": v["pikpak_dir"].get().strip(),
                        "enabled": en.get()})
            if index is None:
                self.cfg["folders"].append(rec)
            else:
                self.cfg["folders"][index] = rec
            self.save_config(); self.refresh_folders()
            win.destroy()

        bb = ttk.Frame(body, style="Card.TFrame")
        bb.grid(row=r, column=0, columnspan=3, sticky="e", pady=(16, 0))
        if old.get("url"):
            ttk.Button(bb, text="↗  Mở trên Fshare", command=lambda: self._check_firefox() and open_in_firefox(
                self.cfg["firefox_path"], old["url"])).pack(side="left", padx=(0, 8))
        ttk.Button(bb, text="Hủy", command=win.destroy).pack(side="left")
        ttk.Button(bb, text="✓  Lưu", style="Accent.TButton", command=save).pack(side="left", padx=(8, 0))
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<Control-s>", save)
        win.update_idletasks()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - win.winfo_reqwidth()) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - win.winfo_reqheight()) // 3)
        win.geometry(f"+{x}+{y}")
        win.grab_set()
        first.focus_set()

    def folder_export(self):
        p = filedialog.asksaveasfilename(title="Xuất danh sách thư mục", defaultextension=".json",
                                         initialfile=f"FshareFolders_{dt.date.today():%Y%m%d}.json",
                                         filetypes=[("JSON", "*.json")])
        if p:
            save_json(p, {"app": APP_NAME, "exported": dt.datetime.now().isoformat(timespec="seconds"),
                          "folders": self.cfg["folders"]})
            messagebox.showinfo(APP_NAME, f"Đã xuất {len(self.cfg['folders'])} thư mục.")

    def folder_import(self):
        p = filedialog.askopenfilename(title="Nhập danh sách thư mục", filetypes=[("JSON", "*.json")])
        if not p:
            return
        data = load_json(p, {})
        items = data.get("folders") if isinstance(data, dict) else data
        if not isinstance(items, list):
            messagebox.showwarning(APP_NAME, "File không đúng định dạng.")
            return
        have = {get_code(f.get("url")) for f in self.cfg["folders"]}
        added = 0
        for f in items:
            code = get_code((f or {}).get("url", ""))
            if code and code not in have:
                self.cfg["folders"].append(f)
                have.add(code)
                added += 1
        self.save_config(); self.refresh_folders()
        messagebox.showinfo(APP_NAME, f"Đã thêm {added} thư mục, bỏ qua {len(items) - added} thư mục đã có.")

    def folder_bulk(self):
        win = tk.Toplevel(self)
        win.title("Dán nhiều link")
        win.geometry("720x420")
        win.configure(bg=C_CARD)
        win.transient(self)
        ttk.Label(win, text="Mỗi dòng 1 link. Có thể ghi tên trước:   Tên phim | https://www.fshare.vn/folder/XXXX",
                  style="Muted.TLabel").pack(anchor="w", padx=12, pady=(12, 6))
        fr = tk.Frame(win, bg=C_BORDER, padx=1, pady=1)
        fr.pack(fill="both", expand=True, padx=12)
        txt = tk.Text(fr, wrap="none", relief="flat", font=("Consolas", 10), padx=8, pady=6)
        txt.pack(fill="both", expand=True)

        def ok():
            added = 0
            for line in txt.get("1.0", "end").splitlines():
                line = line.strip()
                if not line:
                    continue
                name, url = ("", line)
                if "|" in line:
                    name, url = [x.strip() for x in line.split("|", 1)]
                added += self._add_folder(name, url)
            self.save_config(); self.refresh_folders()
            messagebox.showinfo(APP_NAME, f"Đã thêm {added} thư mục.", parent=win)
            win.destroy()
        ttk.Button(win, text="＋  Thêm", style="Accent.TButton", command=ok).pack(pady=10)

    def _build_settings(self):
        f = self.tab_settings
        self.ff_var = tk.StringVar(value=self.cfg.get("firefox_path", ""))
        self.delay_var = tk.IntVar(value=int(self.cfg.get("delay", 4)))
        self.ext_var = tk.StringVar(value=self.cfg.get("extensions", ""))
        self.autoclick_var = tk.BooleanVar(value=bool(self.cfg.get("idm_autoclick", True)))
        self.idm_to_var = tk.IntVar(value=int(self.cfg.get("idm_timeout", 25)))
        self.ff_warm_var = tk.IntVar(value=int(self.cfg.get("ff_warmup", 8)))
        self.ffdl_var = tk.BooleanVar(value=bool(self.cfg.get("watch_ff_downloads", True)))
        self.watch_var = tk.StringVar(value=self.cfg.get("watch_folders", ""))
        self.close_ff_var = tk.StringVar(value=self.cfg.get("close_firefox", CLOSE_FF_CHOICES[0]))
        self.idm_win_var = tk.StringVar(value=self.cfg.get("idm_window", IDM_WINDOW_CHOICES[0]))
        self.notify_var = tk.BooleanVar(value=bool(self.cfg.get("notify", True)))
        self.tray_var = tk.BooleanVar(value=bool(self.cfg.get("tray_on_close", True)))
        self.startwin_var = tk.BooleanVar(value=bool(self.cfg.get("start_with_windows", False)))
        self.startscan_var = tk.BooleanVar(value=bool(self.cfg.get("startup_scan", True)))
        self.organize_var = tk.BooleanVar(value=bool(self.cfg.get("organize", False)))
        self.lib_var = tk.StringVar(value=self.cfg.get("library_root", ""))
        self.pk_on_var = tk.BooleanVar(value=bool(self.cfg.get("pikpak_on", False)))
        self.web_on_var = tk.BooleanVar(value=bool(self.cfg.get("web_on", True)))
        self.web_port_var = tk.IntVar(value=int(self.cfg.get("web_port", 8080) or 8080))
        self.web_pin_var = tk.StringVar(value=str(self.cfg.get("web_pin", "") or ""))
        self.pk_root_var = tk.StringVar(value=self.cfg.get("pikpak_root", ""))
        self.backup_on_var = tk.BooleanVar(value=bool(self.cfg.get("backup_on", True)))
        self.pk_status = tk.StringVar(value="")
        self.bk_status = tk.StringVar(value=("Lần sao lưu gần nhất: " + self.cfg["last_backup"])
                                      if self.cfg.get("last_backup") else "Chưa sao lưu lần nào.")
        self.web_url_var = tk.StringVar(value="  ".join(self.web_urls()))

        # ----- thanh chọn mục
        top = ttk.Frame(f, style="Card.TFrame")
        top.pack(fill="x")
        seg = tk.Frame(top, bg="#e6eaee", padx=3, pady=3)
        seg.pack(side="left")
        self._set_tabs = {}
        body = ttk.Frame(f, style="Card.TFrame")
        body.pack(fill="both", expand=True, pady=(12, 0))
        self._set_pages = {}
        for key, text in (("dl", "⬇  Tải về"), ("store", "▤  Lưu trữ & PikPak"),
                          ("auto", "⚙  Tự động & Web"), ("machine", "⌂  Máy này & Sao lưu")):
            lb = tk.Label(seg, text=text, bg="#e6eaee", fg=C_MUTED, font=(FONT, 10, "bold"), padx=14, pady=6,
                          cursor="hand2")
            lb.pack(side="left", padx=1)
            lb.bind("<Button-1>", lambda e, k=key: self._settings_page(k))
            self._set_tabs[key] = lb
            pg = ttk.Frame(body, style="Card.TFrame")
            self._set_pages[key] = pg

        class G:
            def __init__(self, parent):
                self.p, self.r = parent, 0
                parent.columnconfigure(1, weight=1)

            def section(self, text, sub=None):
                ttk.Label(self.p, text=text, style="Section.TLabel").grid(
                    row=self.r, column=0, columnspan=3, sticky="w", pady=(16 if self.r else 0, 2))
                self.r += 1
                if sub:
                    ttk.Label(self.p, text=sub, style="Muted.TLabel", wraplength=760, justify="left").grid(
                        row=self.r, column=0, columnspan=3, sticky="w", pady=(0, 6))
                    self.r += 1

            def entry(self, label, var, browse=None, hint=None, show=None):
                ttk.Label(self.p, text=label).grid(row=self.r, column=0, sticky="w", pady=5, padx=(0, 12))
                ttk.Entry(self.p, textvariable=var, show=show or "").grid(row=self.r, column=1, sticky="we")
                if browse:
                    ttk.Button(self.p, text="Chọn...", command=browse).grid(row=self.r, column=2, padx=(8, 0))
                self.r += 1
                if hint:
                    self.hint(hint)

            def spin(self, label, var, lo, hi):
                ttk.Label(self.p, text=label).grid(row=self.r, column=0, sticky="w", pady=5, padx=(0, 12))
                ttk.Spinbox(self.p, from_=lo, to=hi, width=7, textvariable=var).grid(row=self.r, column=1, sticky="w")
                self.r += 1

            def check(self, text, var, state="normal"):
                ttk.Checkbutton(self.p, text=text, variable=var, state=state).grid(
                    row=self.r, column=0, columnspan=3, sticky="w", pady=4)
                self.r += 1

            def combo(self, label, var, values, width=26):
                ttk.Label(self.p, text=label).grid(row=self.r, column=0, sticky="w", pady=5, padx=(0, 12))
                ttk.Combobox(self.p, textvariable=var, state="readonly", width=width, values=values).grid(
                    row=self.r, column=1, sticky="w")
                self.r += 1

            def hint(self, text):
                ttk.Label(self.p, text=text, style="Muted.TLabel", wraplength=760, justify="left").grid(
                    row=self.r, column=1, columnspan=2, sticky="w", pady=(0, 4))
                self.r += 1

            def frame(self):
                fr = ttk.Frame(self.p, style="Card.TFrame")
                fr.grid(row=self.r, column=0, columnspan=3, sticky="w", pady=(6, 0))
                self.r += 1
                return fr

            def label(self, var, **kw):
                ttk.Label(self.p, textvariable=var, wraplength=760, justify="left", **kw).grid(
                    row=self.r, column=0, columnspan=3, sticky="w", pady=(4, 0))
                self.r += 1

        # ----- 1. Tải về
        g = G(self._set_pages["dl"])
        g.section("Firefox", "Firefox phải đăng nhập sẵn Fshare VIP, có tiện ích IDM Integration Module "
                             "và bật \"Tải trực tiếp\" trong tài khoản Fshare.")
        g.entry("File firefox.exe", self.ff_var, self._browse_ff)
        g.spin("Chờ giữa 2 file (giây)", self.delay_var, 1, 60)
        g.spin("Chờ Firefox khởi động (giây)", self.ff_warm_var, 0, 60)
        g.hint("Khi Firefox đang tắt, tool mở trước 1 tab trống và chờ tiện ích IDM nạp xong mới mở link "
               "(Firefox mới cập nhật hay bị tự tải). Bị Firefox tự tải thì tăng số này lên 12-15.")
        g.entry("Đuôi file cần tải", self.ext_var, hint="Cách nhau bằng dấu cách; để trống = mọi loại file")
        g.combo("Sau khi IDM nhận link", self.close_ff_var, CLOSE_FF_CHOICES)
        g.hint("\"Chỉ đóng tab Fshare\": chỉ đóng tab tool vừa mở, các tab khác giữ nguyên.")
        g.section("IDM", "Nếu IDM chạy bằng quyền Administrator thì tool cũng phải chạy bằng quyền Administrator.")
        g.check("Tự bấm \"Bắt đầu tải\" (Start Download), không có nút thì nhấn Enter", self.autoclick_var)
        g.spin("Chờ hộp thoại IDM tối đa (giây)", self.idm_to_var, 5, 120)
        g.combo("Cửa sổ tải của IDM", self.idm_win_var, IDM_WINDOW_CHOICES)
        g.entry("Thư mục IDM lưu phim", self.watch_var, self._browse_watch,
                hint="Tool dò thư mục này để biết file nào tải xong. Nhiều thư mục cách nhau bằng dấu ;")
        g.check("Theo dõi cả thư mục Downloads của Firefox (lỡ Firefox tự tải thì vẫn copy / upload)", self.ffdl_var)

        # ----- 2. Lưu trữ & PikPak
        g = G(self._set_pages["store"])
        g.section("Ổ cứng (HDD)", "Tải xong, file được COPY vào <Thư mục phim>\\<Tên phim>\\ (file gốc vẫn ở thư mục IDM).")
        g.check("Tải xong tự copy file vào thư mục riêng theo tên phim (giữ file gốc ở thư mục IDM)", self.organize_var)
        g.entry("Thư mục phim", self.lib_var, self._browse_lib,
                hint="VD: D:\\Hoạt hình  →  D:\\Hoạt hình\\Doraemon\\Doraemon Tap 812.mkv")
        g.section("PikPak", "Upload song song lên PikPak/<Thư mục gốc>/<Tên phim>/ (qua rclone, miễn phí). "
                            "Tài khoản được nhớ, mở tool là tự đăng nhập và kiểm tra kết nối.")
        g.check("Tải xong tự upload lên PikPak", self.pk_on_var)
        fr = g.frame()
        self.rclone_status = tk.StringVar()
        ttk.Label(fr, textvariable=self.rclone_status, style="Muted.TLabel").pack(side="left")
        self.rclone_dl_btn = ttk.Button(fr, text="⬇  Tải rclone.exe về thư mục tool", style="Small.TButton",
                                        command=self.get_rclone)
        self._refresh_rclone_status()
        g.entry("Thư mục gốc trên PikPak", self.pk_root_var, self.open_pikpak_browser)
        fr = g.frame()
        ttk.Button(fr, text="⚙  Tài khoản PikPak...", command=self.open_pikpak_login).pack(side="left")
        ttk.Button(fr, text="✓  Kiểm tra kết nối", command=lambda: self.check_pikpak(silent=False)).pack(
            side="left", padx=8)
        g.label(self.pk_status, foreground=C_ACCENT, font=(FONT, 10, "bold"))

        # ----- 3. Tự động & Web
        g = G(self._set_pages["auto"])
        g.section("Tự động")
        fr = g.frame()
        ttk.Checkbutton(fr, text="Tự quét & tải mỗi", variable=self.auto_var, command=self._auto_changed).pack(side="left")
        cb = ttk.Combobox(fr, textvariable=self.interval_var, values=INTERVAL_CHOICES, width=4, state="readonly")
        cb.pack(side="left", padx=4)
        cb.bind("<<ComboboxSelected>>", lambda e: self._auto_changed())
        ttk.Label(fr, text="phút  (tool phải đang mở hoặc chạy dưới khay)").pack(side="left")
        g.check("Khởi động cùng Windows (chạy ẩn dưới khay)", self.startwin_var)
        g.check("Quét & tải ngay sau khi khởi động (chờ 1 phút cho có mạng)", self.startscan_var)
        tray_state = "normal" if TRAY_OK else "disabled"
        g.check("Bấm X thì thu nhỏ xuống khay hệ thống (góc đồng hồ)", self.tray_var, tray_state)
        g.check("Thông báo Windows khi có file mới / tải xong / upload xong", self.notify_var, tray_state)
        g.section("Giao diện web (điện thoại / tiện ích Firefox)",
                  "Nên đặt mã PIN. Cùng Wi-Fi: mở địa chỉ bên dưới trên điện thoại. Ở ngoài nhà: dùng Tailscale.")
        g.check("Bật giao diện web", self.web_on_var)
        fr = g.frame()
        ttk.Label(fr, text="Cổng").pack(side="left")
        ttk.Spinbox(fr, from_=1024, to=65535, width=7, textvariable=self.web_port_var).pack(side="left", padx=(6, 16))
        ttk.Label(fr, text="Mã PIN").pack(side="left")
        ttk.Entry(fr, textvariable=self.web_pin_var, width=14, show="•").pack(side="left", padx=6)
        ul = ttk.Label(self._set_pages["auto"], textvariable=self.web_url_var, foreground=C_ACCENT, cursor="hand2",
                       font=(FONT, 10, "bold"), wraplength=760)
        ul.grid(row=g.r, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ul.bind("<Button-1>", lambda e: self._open_web_local())
        g.r += 1
        fr = g.frame()
        ttk.Button(fr, text="⛨  Cho phép qua tường lửa Windows (cần quyền Admin)", command=self.open_firewall).pack(side="left")
        ttk.Button(fr, text="↗  Mở thử trên máy này", command=self._open_web_local).pack(side="left", padx=8)
        g.hint("Điện thoại / máy khác không vào được: (1) bấm \"Cho phép qua tường lửa\", (2) điện thoại phải cùng Wi-Fi "
               "(tắt 4G, tắt VPN / iCloud Private Relay), (3) gõ đúng địa chỉ có http:// và :cổng, (4) Wi-Fi của Windows "
               "nên để chế độ Private (Riêng tư).")

        # ----- 4. Máy này & Sao lưu
        g = G(self._set_pages["machine"])
        g.section(f"Máy này: {MACHINE}",
                  "Tool để trên ổ cứng di động, cắm vào nhiều máy: mỗi máy tự nhớ đường dẫn riêng (Firefox, thư mục "
                  "IDM, thư mục phim, rclone.exe, thư mục PikPak...). Đường dẫn nằm trên chính ổ di động được lưu "
                  "theo kiểu không phụ thuộc ký tự ổ (E:, F:...).")
        pw = ttk.Frame(self._set_pages["machine"], style="Card.TFrame")
        pw.grid(row=g.r, column=0, columnspan=3, sticky="we")
        g.r += 1
        wrap, self.prof_tree = self._tree(pw, ("name", "updated", "paths"), ("MÁY", "CẬP NHẬT", "THƯ MỤC PHIM / IDM"),
                                          (160, 130, 460), height=4, stretch=("paths",))
        wrap.pack(fill="x")
        fr = g.frame()
        ttk.Button(fr, text="⧉  Chép thiết lập từ máy đã chọn", command=self.copy_profile_selected).pack(side="left")
        ttk.Button(fr, text="✕  Xóa hồ sơ máy đã chọn", style="Danger.TButton",
                   command=self.delete_profile_selected).pack(side="left", padx=8)
        g.section("Sao lưu dữ liệu", "Phòng khi ổ cứng hỏng: config.json (thư mục, cài đặt), owned.json (Danh sách "
                                      "đã có), history.json (lịch sử) được nén và đưa lên PikPak/"
                                      f"<Thư mục gốc>/{BACKUP_DIR}/.")
        g.check("Sau mỗi lần tải xong, tự sao lưu dữ liệu lên PikPak", self.backup_on_var)
        fr = g.frame()
        ttk.Button(fr, text="☁  Sao lưu lên PikPak ngay", style="Accent.TButton", command=self.backup_now).pack(side="left")
        ttk.Button(fr, text="⟲  Khôi phục từ PikPak...", command=self.restore_from_pikpak).pack(side="left", padx=8)
        ttk.Button(fr, text="⇩  Sao lưu ra file...", command=self.backup_to_file).pack(side="left")
        ttk.Button(fr, text="⇧  Khôi phục từ file...", command=self.restore_from_file).pack(side="left", padx=8)
        g.label(self.bk_status, style="Muted.TLabel")
        g.section("Thư mục Data", "Mọi dữ liệu (cấu hình, Danh sách đã có, lịch sử, đăng nhập PikPak, nhật ký) nằm trong "
                                  "thư mục Data cạnh tool. Lên bản mới: chép thư mục Data của bản cũ sang bản mới là xong.")
        fr = g.frame()
        ttk.Button(fr, text="▸  Mở thư mục Data", command=self.open_data_dir).pack(side="left")
        ttk.Label(fr, text=str(DATA_DIR), style="Muted.TLabel").pack(side="left", padx=10)
        self.refresh_profiles()

        # ----- thanh lưu
        bottom = tk.Frame(f, bg=C_SOFT, highlightbackground=C_SOFT_BORDER, highlightthickness=1)
        bottom.pack(fill="x", side="bottom", pady=(10, 0))
        bi = tk.Frame(bottom, bg=C_SOFT, padx=10, pady=8)
        bi.pack(fill="x")
        ttk.Button(bi, text="✓  Lưu cài đặt", style="Success.TButton",
                   command=lambda: (self.save_config(), self.refresh_profiles(),
                                    self.status_var.set("✔ Đã lưu cài đặt cho máy " + MACHINE))).pack(side="left")
        ttk.Button(bi, text="↗  Mở thử Fshare trên Firefox",
                   command=lambda: self._check_firefox() and open_in_firefox(self.ff_var.get(), "https://www.fshare.vn/")
                   ).pack(side="left", padx=8)
        ttk.Button(bi, text="◉  Thử thông báo",
                   command=lambda: (self.save_config(), self.notify(APP_TITLE, "Thông báo hoạt động bình thường."))
                   ).pack(side="left")
        self._settings_page("dl")

    def _settings_page(self, key):
        for k, pg in self._set_pages.items():
            pg.pack_forget()
            on = k == key
            self._set_tabs[k].config(bg=C_CARD if on else "#e6eaee", fg=C_ACCENT if on else C_MUTED)
        self._set_pages[key].pack(fill="both", expand=True)

    # ================================================================ HỒ SƠ MÁY (ổ di động)
    def _apply_profile(self, prof):
        for k in MACHINE_KEYS:
            if k in prof:
                self.cfg[k] = from_portable(prof[k]) if k in PATH_KEYS else prof[k]

    def _init_machine_profile(self):
        """Áp dụng đường dẫn riêng của máy đang chạy. Trả về True nếu là máy mới (cần hỏi)."""
        profiles = self.cfg.setdefault("profiles", {})
        if MACHINE in profiles:
            self._apply_profile(profiles[MACHINE])
            return False
        if not profiles:
            return False               # máy đầu tiên: dùng luôn cấu hình hiện có
        # máy mới: lấy theo máy dùng gần nhất, rồi tự dò lại những đường dẫn không có trên máy này
        last = max(profiles.values(), key=lambda p: p.get("updated", 0))
        self._apply_profile(last)
        self._fix_missing_paths()
        return True

    def _fix_missing_paths(self):
        if not os.path.isfile(self.cfg.get("firefox_path", "")):
            self.cfg["firefox_path"] = find_firefox()
        if not any(os.path.isdir(x.strip()) for x in self.cfg.get("watch_folders", "").split(";") if x.strip()):
            self.cfg["watch_folders"] = find_idm_folders()
        if self.cfg.get("library_root") and not os.path.isdir(self.cfg["library_root"]):
            self.cfg["organize"] = False

    def _store_machine_profile(self):
        prof = {k: (to_portable(self.cfg.get(k, "")) if k in PATH_KEYS else self.cfg.get(k)) for k in MACHINE_KEYS}
        prof["updated"] = time.time()
        self.cfg.setdefault("profiles", {})[MACHINE] = prof

    def refresh_profiles(self):
        if not hasattr(self, "prof_tree"):
            return
        self.prof_tree.delete(*self.prof_tree.get_children())
        for i, (name, p) in enumerate(sorted(self.cfg.get("profiles", {}).items(),
                                             key=lambda kv: -kv[1].get("updated", 0))):
            when = dt.datetime.fromtimestamp(p.get("updated", 0)).strftime("%H:%M %d/%m/%Y") if p.get("updated") else ""
            paths = f"{p.get('library_root') or '-'}   |   IDM: {p.get('watch_folders') or '-'}"
            tags = ["odd"] if i % 2 else []
            if name == MACHINE:
                tags.append("done")
            self.prof_tree.insert("", "end", iid=name, values=(name + ("  (máy này)" if name == MACHINE else ""),
                                                               when, paths), tags=tags)

    def copy_profile_selected(self):
        sel = self.prof_tree.selection()
        if not sel or sel[0] == MACHINE:
            messagebox.showinfo(APP_NAME, "Chọn 1 máy KHÁC trong danh sách để chép thiết lập.")
            return
        self._apply_profile(self.cfg["profiles"][sel[0]])
        self._fix_missing_paths()
        self._reload_setting_vars()
        self.save_config(); self.refresh_profiles()
        messagebox.showinfo(APP_NAME, f"Đã chép thiết lập từ máy \"{sel[0]}\". Kiểm tra lại các đường dẫn rồi bấm Lưu.")

    def delete_profile_selected(self):
        sel = self.prof_tree.selection()
        if not sel or sel[0] == MACHINE:
            messagebox.showinfo(APP_NAME, "Chọn 1 máy khác (không xóa được hồ sơ của máy đang dùng).")
            return
        if messagebox.askyesno(APP_NAME, f"Xóa hồ sơ máy \"{sel[0]}\"?"):
            self.cfg["profiles"].pop(sel[0], None)
            self.save_config(); self.refresh_profiles()

    def _reload_setting_vars(self):
        for var, key in ((self.ff_var, "firefox_path"), (self.watch_var, "watch_folders"),
                         (self.lib_var, "library_root"),
                         (self.pk_root_var, "pikpak_root")):
            var.set(self.cfg.get(key, "") or "")
        self.organize_var.set(bool(self.cfg.get("organize")))
        self.web_on_var.set(bool(self.cfg.get("web_on", True)))
        self.web_port_var.set(int(self.cfg.get("web_port", 8080) or 8080))
        self.startwin_var.set(bool(self.cfg.get("start_with_windows")))

    def _new_machine_dialog(self):
        others = [n for n in self.cfg.get("profiles", {}) if n != MACHINE]
        msg = (f"Đây là lần đầu tool chạy trên máy \"{MACHINE}\".\n\n"
               f"Tool đã chép thiết lập từ máy \"{others[0] if others else '?'}\" và tự dò lại Firefox, thư mục IDM, "
               "rclone.exe trên máy này.\n\nMở Cài đặt để kiểm tra đường dẫn (thư mục phim, thư mục IDM...)?")
        if messagebox.askyesno(APP_NAME + " - Máy mới", msg):
            self.select_page("settings")
            self._settings_page("machine")
        self.save_config(); self.refresh_profiles()

    # ================================================================ PIKPAK: tài khoản + kết nối
    def _set_pk_status(self, text, ok):
        color = "#86efac" if ok is True else ("#fca5a5" if ok is False else C_SIDE_MUTED)
        try:
            self.side_pk.config(text=text, fg=color)
        except (AttributeError, tk.TclError):
            pass
        if hasattr(self, "pk_status"):
            self.pk_status.set(text)

    def check_pikpak(self, silent=True):
        """Tự đăng nhập (từ tài khoản đã nhớ) và kiểm tra kết nối PikPak - chạy nền."""
        self._set_pk_status("☁ PikPak: đang kết nối...", None)
        cfg = json.loads(json.dumps(self.cfg))

        def job():
            r = find_rclone(cfg.get("rclone_path", ""))
            if not r:
                self.q.put(("pkstatus", "☁ PikPak: chưa có rclone.exe", False))
                return
            remote = cfg.get("pikpak_remote", "pikpak") or "pikpak"
            conf_ok = RCLONE_CONF.is_file() and f"[{remote}]" in RCLONE_CONF.read_text(encoding="utf-8", errors="ignore")
            if not conf_ok:
                # chuyển cấu hình cũ (rclone mặc định của Windows) vào thư mục tool nếu có
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    out = subprocess.run([r, "config", "file"], capture_output=True, text=True, timeout=20,
                                         creationflags=flags).stdout
                    old = Path(out.strip().splitlines()[-1].strip()) if out.strip() else None
                    if old and old.is_file() and f"[{remote}]" in old.read_text(encoding="utf-8", errors="ignore"):
                        shutil.copy(old, RCLONE_CONF)
                        conf_ok = True
                except Exception:
                    pass
            user, pw = cfg.get("pikpak_user", ""), dec_secret(cfg.get("pikpak_pass", ""))
            if not conf_ok and user and pw:
                code, out, err = run_rclone(r, ["config", "create", remote, "pikpak", "user", user, "pass", pw,
                                                "--obscure", "--non-interactive"], 60)
                conf_ok = code == 0
            if not conf_ok:
                self.q.put(("pkstatus", "☁ PikPak: chưa đăng nhập (bấm để thiết lập)", None))
                return
            code, out, err = run_rclone(r, ["lsd", f"{remote}:"], 60)
            if code == 0:
                self.q.put(("pkstatus", "☁ PikPak: ✔ đã kết nối" + (f" ({user})" if user else ""), True))
            else:
                self.q.put(("pkstatus", "☁ PikPak: ⚠ lỗi kết nối - " + (err or out).strip()[-120:], False))
            if not silent:
                self.q.put(("log", "PikPak: " + ("kết nối OK" if code == 0 else "lỗi kết nối")))
        threading.Thread(target=job, daemon=True).start()

    # ================================================================ SAO LƯU / KHÔI PHỤC
    def _backup_remote(self):
        return f"{self.cfg.get('pikpak_remote', 'pikpak') or 'pikpak'}:" + pikpak_join(self.cfg.get("pikpak_root", ""),
                                                                                       BACKUP_DIR)

    def schedule_backup(self):
        if not self.cfg.get("backup_on", True) or self._backup_pending:
            return
        if not find_rclone(self.cfg.get("rclone_path", "")):
            return
        self._backup_pending = True
        self.after(20000, lambda: self.up_q.put("__backup__"))   # gom nhiều file tải xong thành 1 lần sao lưu

    def backup_now(self):
        if not self._need_rclone():
            return
        self.save_config()
        self.bk_status.set("Đang sao lưu lên PikPak...")
        self._backup_pending = True
        self.up_q.put("__backup__")

    def _do_backup(self):
        import tempfile
        try:
            r = find_rclone(self.cfg.get("rclone_path", ""))
            if not r:
                raise RuntimeError("chưa có rclone.exe")
            tmp = Path(tempfile.gettempdir()) / "FshareAutoDownload_backup.zip"
            self.call_ui(lambda: save_json(CONFIG_FILE, self.cfg) if not self._no_save else None)
            make_backup_zip(tmp)
            dest = self._backup_remote()
            code, out, err = run_rclone(r, ["copyto", str(tmp), f"{dest}/FshareAutoDownload_backup_latest.zip"], 300)
            if code != 0:
                raise RuntimeError((err or out).strip()[-150:])
            run_rclone(r, ["copyto", str(tmp), f"{dest}/FshareAutoDownload_backup_{dt.date.today():%Y-%m-%d}.zip"], 300)
            self.q.put(("backup", True, dt.datetime.now().strftime("%H:%M %d/%m/%Y")))
        except Exception as e:
            self.q.put(("backup", False, str(e)))

    def _on_backup_done(self, ok, info):
        self._backup_pending = False
        if ok:
            self.cfg["last_backup"] = info
            save_json(CONFIG_FILE, self.cfg)
            self.side_bk.config(text="⟲ Sao lưu: " + info)
            self.bk_status.set(f"✔ Lần sao lưu gần nhất: {info}  →  {self._backup_remote()}")
            self.log(f"⟲ Đã sao lưu dữ liệu lên PikPak ({info}).")
        else:
            self.side_bk.config(text="⟲ Sao lưu lỗi")
            self.bk_status.set("⚠ Sao lưu lỗi: " + info)
            self.log("⚠ Sao lưu lên PikPak lỗi: " + info)

    def backup_to_file(self):
        self.save_config()
        p = filedialog.asksaveasfilename(title="Sao lưu dữ liệu", defaultextension=".zip",
                                         initialfile=f"FshareAutoDownload_backup_{dt.date.today():%Y-%m-%d}.zip",
                                         filetypes=[("Zip", "*.zip")])
        if p:
            make_backup_zip(p)
            messagebox.showinfo(APP_NAME, "Đã sao lưu ra file:\n" + p)

    def restore_from_file(self):
        p = filedialog.askopenfilename(title="Chọn file sao lưu", filetypes=[("Zip", "*.zip")])
        if p:
            self._restore_zip(p)

    def restore_from_pikpak(self):
        r = self._need_rclone()
        if not r:
            return
        import tempfile
        tmp = Path(tempfile.gettempdir()) / "FshareAutoDownload_restore.zip"
        self.bk_status.set("Đang tải bản sao lưu từ PikPak...")
        self.update()
        code, out, err = run_rclone(r, ["copyto", f"{self._backup_remote()}/FshareAutoDownload_backup_latest.zip",
                                        str(tmp)], 300)
        if code != 0 or not tmp.is_file():
            self.bk_status.set("⚠ Không tải được bản sao lưu: " + (err or out).strip()[-150:])
            return
        self._restore_zip(str(tmp))

    def open_data_dir(self):
        try:
            DATA_DIR.mkdir(exist_ok=True)
            if os.name == "nt":
                os.startfile(str(DATA_DIR))
            else:
                subprocess.Popen(["xdg-open", str(DATA_DIR)])
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Không mở được thư mục Data: {e}")

    # ================================================================ CẬP NHẬT PHẦN MỀM
    def check_update(self, silent=True):
        repo = (self.cfg.get("update_repo") or "").strip()
        if getattr(self, "_upd_busy", False):
            return
        if not repo:
            if not silent:
                self._update_menu()
            return
        self._upd_busy = True
        if not silent:
            self.status_var.set("Đang kiểm tra bản mới...")

        def job():
            try:
                tag, notes, url = github_latest(repo)
                self.q.put(("call", lambda: self._update_found(tag, notes, url, silent), (), {}, threading.Event()))
            except Exception as e:
                err = str(e)
                self.q.put(("call", lambda: self._update_error(err, silent), (), {}, threading.Event()))
            finally:
                self._upd_busy = False
        threading.Thread(target=job, daemon=True).start()

    def _update_error(self, err, silent):
        self.log(f"Kiểm tra cập nhật: {err}")
        if not silent:
            self.status_var.set("Không kiểm tra được bản mới.")
            self._update_menu(err)

    def _update_found(self, tag, notes, url, silent):
        newer = ver_tuple(tag) > ver_tuple(APP_VERSION)
        if newer:
            self.top_upd.config(text=f"⟳  Có bản {tag}", bg=C_ACCENT, fg="white", highlightbackground=C_ACCENT)
            self.log(f"⟳ Có bản mới {tag} (đang dùng {APP_VERSION}).")
            self.notify("Có bản mới " + tag, "Bấm nút Cập nhật trên tool để cài.")
        if silent:
            return
        if not newer:
            self.status_var.set(f"Đang dùng bản mới nhất ({APP_VERSION}).")
            self._update_menu(f"Đang dùng bản mới nhất ({APP_VERSION.split(' by')[0]}). Bản trên GitHub: {tag}.")
            return
        msg = (f"Có bản mới {tag} (đang dùng {APP_VERSION.split(' by')[0]}).\n\n"
               + (notes.strip()[:900] + "\n\n" if notes.strip() else "")
               + "Tải về và cập nhật ngay? Thư mục Data (cấu hình, Danh sách đã có, lịch sử) được giữ nguyên.\n"
                 "Tool sẽ tự khởi động lại.")
        if url and messagebox.askyesno(APP_NAME + " - Cập nhật", msg):
            self._download_update(url, tag)

    def _update_menu(self, info=""):
        """Hộp thoại nhỏ: kiểm tra trên GitHub / cập nhật từ file zip."""
        win = tk.Toplevel(self)
        win.title("Cập nhật phần mềm")
        win.configure(bg=C_CARD)
        win.transient(self)
        win.resizable(False, False)
        b = ttk.Frame(win, style="Card.TFrame", padding=18)
        b.pack(fill="both", expand=True)
        ttk.Label(b, text="Cập nhật phần mềm", style="Section.TLabel").pack(anchor="w")
        ttk.Label(b, text=f"Đang dùng: {APP_VERSION}", style="Muted.TLabel").pack(anchor="w", pady=(2, 8))
        if info:
            ttk.Label(b, text=info, wraplength=460, justify="left").pack(anchor="w", pady=(0, 10))
        ttk.Label(b, text="Nguồn cập nhật (GitHub: tên-tài-khoản/tên-repo)", style="Muted.TLabel").pack(anchor="w")
        repo = tk.StringVar(value=self.cfg.get("update_repo", ""))
        ttk.Entry(b, textvariable=repo, width=50).pack(fill="x", pady=(2, 6))
        auto = tk.BooleanVar(value=bool(self.cfg.get("update_check", True)))
        ttk.Checkbutton(b, text="Tự kiểm tra bản mới mỗi lần mở tool", variable=auto).pack(anchor="w")

        def save():
            self.cfg["update_repo"] = repo.get().strip()
            self.cfg["update_check"] = auto.get()
            save_json(CONFIG_FILE, self.cfg)
        fr = ttk.Frame(b, style="Card.TFrame")
        fr.pack(fill="x", pady=(12, 0))
        ttk.Button(fr, text="⟳  Kiểm tra trên GitHub", style="Accent.TButton",
                   command=lambda: (save(), win.destroy(), self.check_update(silent=False))).pack(side="left")
        ttk.Button(fr, text="⇧  Cập nhật từ file zip...",
                   command=lambda: (save(), win.destroy(), self.update_from_file())).pack(side="left", padx=8)
        ttk.Label(b, text="Có file FshareAutoDownload_vX.Y.zip (VD: tải từ Claude) -> bấm \"Cập nhật từ file zip\". "
                          "Thư mục Data luôn được giữ nguyên.", style="Muted.TLabel", wraplength=460,
                  justify="left").pack(anchor="w", pady=(10, 0))
        win.bind("<Escape>", lambda e: win.destroy())

    def update_from_file(self):
        p = filedialog.askopenfilename(title="Chọn file cập nhật (FshareAutoDownload_vX.Y.zip)",
                                       filetypes=[("File zip", "*.zip")])
        if p:
            self._apply_update(p)

    def _download_update(self, url, tag):
        import tempfile
        dest = Path(tempfile.gettempdir()) / f"FshareAutoDownload_{tag}.zip"
        self.status_var.set(f"Đang tải bản {tag}...")

        def prog(pct, done):
            self.q.put(("status", f"Đang tải bản {tag}... " + (f"{pct}%" if pct >= 0 else human_size(done))))

        def job():
            try:
                download_file(url, dest, prog)
                self.q.put(("call", lambda: self._apply_update(str(dest), ask=False), (), {}, threading.Event()))
            except Exception as e:
                err = str(e)
                self.q.put(("call", lambda: messagebox.showerror(APP_NAME, f"Không tải được bản cập nhật: {err}"),
                            (), {}, threading.Event()))
        threading.Thread(target=job, daemon=True).start()

    def _apply_update(self, zip_path, ask=True):
        if self.worker and self.worker.is_alive():
            messagebox.showwarning(APP_NAME, "Tool đang quét / tải. Đợi xong rồi cập nhật.")
            return
        if ask and not messagebox.askyesno(APP_NAME, "Cập nhật tool từ file:\n" + zip_path +
                                           "\n\nThư mục Data được giữ nguyên. Bản cũ lưu ở thư mục _BanCu. "
                                           "Tool sẽ tự khởi động lại."):
            return
        try:
            self.save_config()
            new_ver, pending_exe = stage_update(zip_path)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Cập nhật không thành công: {e}")
            return
        self.log(f"⟳ Đã cập nhật lên {new_ver or 'bản mới'} - khởi động lại...")
        self._no_save = True
        self.save_history()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        if pending_exe:       # bản exe: thay file exe sau khi tool thoát
            exe = Path(sys.executable)
            bat = BASE / "_capnhat.bat"
            bat.write_text(
                "@echo off\r\n"
                ":wait\r\n"
                f'tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul && (ping -n 2 127.0.0.1 >nul & goto wait)\r\n'
                f'move /y "{pending_exe}" "{exe}" >nul\r\n'
                f'start "" "{exe}" --after-update\r\n'
                'del "%~f0"\r\n', encoding="utf-8")
            subprocess.Popen(["cmd", "/c", str(bat)], creationflags=flags, cwd=str(BASE))
        elif getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable, "--after-update"], cwd=str(BASE))
        else:
            subprocess.Popen([sys.executable, str(BASE / "fshare_auto_idm.py"), "--after-update"], cwd=str(BASE))
        self.quit_app(force=True)

    def _restore_zip(self, path):
        import zipfile
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n in BACKUP_FILES]
                info = z.comment.decode("utf-8", "ignore")
                if not names:
                    raise ValueError("File không phải bản sao lưu của tool.")
                if not messagebox.askyesno(APP_NAME, f"Khôi phục dữ liệu từ bản sao lưu?\n\n{info}\n"
                                                     f"Gồm: {', '.join(names)}\n\nDữ liệu hiện tại sẽ được giữ lại "
                                                     "dạng *.bak. Tool sẽ tự khởi động lại."):
                    return
                for n in names:
                    if (DATA_DIR / n).is_file():
                        shutil.copy(DATA_DIR / n, DATA_DIR / (n + ".bak"))
                    z.extract(n, DATA_DIR)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Không khôi phục được: {e}")
            return
        self._no_save = True
        if getattr(sys, "frozen", False):
            subprocess.Popen([sys.executable])
        else:
            subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        self.quit_app()

    # ================================================================ PIKPAK
    def _rclone(self):
        return find_rclone()

    def _refresh_rclone_status(self):
        if not hasattr(self, "rclone_status"):
            return
        if find_rclone():
            self.rclone_status.set(f"rclone.exe: ✔ có sẵn trong thư mục tool  ({RCLONE_EXE.parent})")
            self.rclone_dl_btn.pack_forget()
        else:
            self.rclone_status.set(f"rclone.exe: ✘ chưa có trong thư mục tool  ({RCLONE_EXE.parent})")
            self.rclone_dl_btn.pack(side="left", padx=10)

    def get_rclone(self, then=None):
        """Tải rclone.exe về thư mục tool (chạy nền)."""
        if getattr(self, "_rclone_dl", False):
            return
        self._rclone_dl = True
        try:
            self.rclone_dl_btn.config(state="disabled")
        except (AttributeError, tk.TclError):
            pass

        def job():
            try:
                download_rclone(lambda pct: self.q.put(("status", f"Đang tải rclone.exe... {pct}%")))
                self.q.put(("call", lambda: (self.log("✔ Đã tải rclone.exe vào thư mục tool."),
                                             self.status_var.set("Đã tải rclone.exe."),
                                             self._refresh_rclone_status(), self.check_pikpak(silent=True),
                                             then and then()), (), {}, threading.Event()))
            except Exception as e:
                msg = f"Không tải được rclone.exe: {e}"
                self.q.put(("call", lambda: (self.log("⚠ " + msg), self.status_var.set(msg),
                                             messagebox.showwarning(APP_NAME, msg + "\n\nTải tay ở https://rclone.org/downloads/ "
                                                                    f"(Windows 64-bit), giải nén, chép rclone.exe vào:\n{RCLONE_EXE.parent}")),
                            (), {}, threading.Event()))
            finally:
                self._rclone_dl = False
                self.q.put(("call", lambda: self.rclone_dl_btn.config(state="normal") if hasattr(self, "rclone_dl_btn") else None,
                            (), {}, threading.Event()))
        threading.Thread(target=job, daemon=True).start()

    def _need_rclone(self, parent=None, then=None):
        self.save_config()
        r = self._rclone()
        if not r:
            if messagebox.askyesno(APP_NAME, "Chưa có rclone.exe trong thư mục tool:\n"
                                             f"{RCLONE_EXE.parent}\n\nTải rclone.exe (miễn phí, ~20MB) về ngay?",
                                   parent=parent):
                self.get_rclone(then)
        return r

    def open_pikpak_login(self):
        r = self._need_rclone()
        if not r:
            return
        win = tk.Toplevel(self)
        win.title("Đăng nhập PikPak")
        win.configure(bg=C_CARD)
        win.transient(self)
        body = ttk.Frame(win, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="Tài khoản PikPak", style="Section.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(body, text="Nhập email / mật khẩu PikPak. Tool nhớ tài khoản (mật khẩu được mã hóa) để mỗi lần mở "
                             "- kể cả trên máy khác khi dùng ổ di động - tự đăng nhập và kiểm tra kết nối.",
                  style="Muted.TLabel", wraplength=420, justify="left").grid(row=1, column=0, columnspan=2, sticky="w",
                                                                              pady=(4, 10))
        u = tk.StringVar(value=self.cfg.get("pikpak_user", ""))
        pw = tk.StringVar(value=dec_secret(self.cfg.get("pikpak_pass", "")))
        remember = tk.BooleanVar(value=True)
        ttk.Label(body, text="Email / tài khoản").grid(row=2, column=0, sticky="w", pady=4)
        eu = ttk.Entry(body, textvariable=u, width=36)
        eu.grid(row=2, column=1, sticky="we", pady=4, padx=(10, 0))
        ttk.Label(body, text="Mật khẩu").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(body, textvariable=pw, show="•", width=36).grid(row=3, column=1, sticky="we", pady=4, padx=(10, 0))
        ttk.Checkbutton(body, text="Ghi nhớ tài khoản và mật khẩu", variable=remember).grid(
            row=4, column=1, sticky="w", padx=(10, 0), pady=(4, 0))
        st = tk.StringVar()
        ttk.Label(body, textvariable=st, style="Muted.TLabel", wraplength=420, justify="left").grid(
            row=5, column=0, columnspan=2, sticky="w", pady=(8, 0))
        remote = self.cfg.get("pikpak_remote", "pikpak") or "pikpak"

        def save():
            if not u.get().strip() or not pw.get():
                st.set("Nhập đủ email và mật khẩu.")
                return
            st.set("Đang đăng nhập...")
            win.update()
            run_rclone(r, ["config", "delete", remote], 20)
            code, out, err = run_rclone(r, ["config", "create", remote, "pikpak", "user", u.get().strip(),
                                            "pass", pw.get(), "--obscure", "--non-interactive"], 60)
            if code != 0:
                st.set("⚠ Không tạo được cấu hình: " + (err or out).strip()[-300:])
                return
            code, out, err = run_rclone(r, ["lsd", f"{remote}:"], 60)
            if code == 0:
                self.cfg["pikpak_user"] = u.get().strip() if remember.get() else ""
                self.cfg["pikpak_pass"] = enc_secret(pw.get()) if remember.get() else ""
                self.save_config()
                self._set_pk_status("☁ PikPak: ✔ đã kết nối (" + u.get().strip() + ")", True)
                messagebox.showinfo(APP_NAME, "Đăng nhập PikPak thành công.", parent=win)
                win.destroy()
            else:
                st.set("⚠ Đăng nhập lỗi: " + (err or out).strip()[-300:])
        bb = ttk.Frame(body, style="Card.TFrame")
        bb.grid(row=6, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ttk.Button(bb, text="Hủy", command=win.destroy).pack(side="left")
        ttk.Button(bb, text="✓  Đăng nhập", style="Accent.TButton", command=save).pack(side="left", padx=(8, 0))
        win.bind("<Return>", lambda e: save())
        win.bind("<Escape>", lambda e: win.destroy())
        win.grab_set()
        eu.focus_set()

    def test_pikpak(self):
        r = self._need_rclone()
        if not r:
            return
        remote = self.cfg.get("pikpak_remote", "pikpak") or "pikpak"
        self.pk_status.set("Đang kiểm tra...")
        self.update()
        code, out, err = run_rclone(r, ["lsd", f"{remote}:"], 60)
        if code == 0:
            n = len([x for x in out.splitlines() if x.strip()])
            self.pk_status.set(f"✔ Kết nối PikPak OK ({n} thư mục ở thư mục gốc).")
        else:
            self.pk_status.set("⚠ Chưa kết nối được. Bấm \"Đăng nhập PikPak...\".  " + (err or out).strip()[-200:])

    def open_pikpak_browser(self):
        r = self._need_rclone()
        if not r:
            return
        remote = self.cfg.get("pikpak_remote", "pikpak") or "pikpak"
        win = tk.Toplevel(self)
        win.title("Chọn thư mục trên PikPak")
        win.configure(bg=C_CARD)
        win.transient(self)
        win.geometry(f"520x460+{self.winfo_rootx() + 200}+{self.winfo_rooty() + 100}")
        body = ttk.Frame(win, style="Card.TFrame", padding=14)
        body.pack(fill="both", expand=True)
        cur = [pikpak_join(self.pk_root_var.get())]
        path_var = tk.StringVar()
        ttk.Label(body, textvariable=path_var, style="Section.TLabel", wraplength=480).pack(anchor="w")
        lf = tk.Frame(body, bg=C_BORDER, padx=1, pady=1)
        lf.pack(fill="both", expand=True, pady=8)
        lb = tk.Listbox(lf, font=(FONT, 10), relief="flat", activestyle="none", selectbackground=C_SEL,
                        selectforeground=C_TEXT)
        lb.pack(fill="both", expand=True)
        info = tk.StringVar()
        ttk.Label(body, textvariable=info, style="Muted.TLabel").pack(anchor="w")

        def load(path):
            info.set("Đang tải danh sách...")
            win.update()
            code, out, err = run_rclone(r, ["lsjson", f"{remote}:{path}", "--dirs-only"], 60)
            if code != 0:
                if path:   # thư mục chưa có -> quay về gốc
                    info.set("Thư mục chưa có trên PikPak, sẽ được tạo khi upload.")
                    cur[0] = path
                    path_var.set("PikPak / " + path)
                    lb.delete(0, "end")
                    lb.insert("end", "↰  ..")
                    return
                info.set("⚠ Lỗi: " + (err or out).strip()[-200:])
                return
            try:
                dirs = sorted((d.get("Name", "") for d in json.loads(out or "[]")), key=natural_key)
            except ValueError:
                dirs = []
            cur[0] = path
            path_var.set("PikPak / " + (path or "(thư mục gốc)"))
            lb.delete(0, "end")
            if path:
                lb.insert("end", "↰  ..")
            for d in dirs:
                lb.insert("end", "▦  " + d)
            info.set(f"{len(dirs)} thư mục. Nhấp đúp để mở.")

        def open_sel(_e=None):
            sel = lb.curselection()
            if not sel:
                return
            t = lb.get(sel[0])
            if t.startswith("↰"):
                load("/".join(cur[0].split("/")[:-1]))
            else:
                load(pikpak_join(cur[0], t[3:]))

        def mkdir():
            from tkinter import simpledialog
            name = simpledialog.askstring(APP_NAME, "Tên thư mục mới:", parent=win)
            if name and name.strip():
                code, out, err = run_rclone(r, ["mkdir", f"{remote}:{pikpak_join(cur[0], name)}"], 60)
                if code != 0:
                    info.set("⚠ " + (err or out).strip()[-200:])
                load(cur[0])

        def choose():
            self.pk_root_var.set(cur[0])
            self.save_config()
            win.destroy()
        lb.bind("<Double-1>", open_sel)
        bb = ttk.Frame(body, style="Card.TFrame")
        bb.pack(fill="x", pady=(8, 0))
        ttk.Button(bb, text="＋  Thư mục mới", command=mkdir).pack(side="left")
        ttk.Button(bb, text="✓  Chọn thư mục này", style="Accent.TButton", command=choose).pack(side="right")
        ttk.Button(bb, text="Hủy", command=win.destroy).pack(side="right", padx=8)
        win.grab_set()
        win.after(100, lambda: load(cur[0]))

    @staticmethod
    def _upload_text(h):
        u = h.get("upload", "")
        return {"pending": "⏳  Chờ upload", "done": "✔  Đã lên PikPak", "error": "⚠  Lỗi upload"}.get(
            u, h.get("upload_msg", "") if u == "uploading" else "")

    def enqueue_upload(self, code, force=False):
        h = self.history.get(code)
        if not h or not (self.cfg.get("pikpak_on") or force):
            return False
        if h.get("upload") in ("pending", "uploading") or (h.get("upload") == "done" and not force):
            return False
        if code in self.up_queued or not h.get("path") or not os.path.isfile(h["path"]):
            return False
        h["upload"] = "pending"
        h.pop("upload_msg", None)
        self.up_queued.add(code)
        self.up_q.put(code)
        return True

    def _resume_uploads(self):
        n = 0
        for code, h in self.history.items():
            if h.get("upload") in ("pending", "uploading") and code not in self.up_queued:
                h["upload"] = ""
                n += self.enqueue_upload(code, force=True)
        if n:
            self.log(f"PikPak: tiếp tục upload {n} file còn dở.")
            self.save_history(); self.refresh_history()

    def history_upload_selected(self):
        if not self._need_rclone():
            return
        n = 0
        for code in self.hist_tree.selection():
            n += self.enqueue_upload(code, force=True)
        self.save_history(); self.refresh_history()
        self.status_var.set(f"Đã thêm {n} file vào hàng chờ upload PikPak." if n else
                            "Không có file nào upload được (chỉ upload file đã tải xong và còn trên máy).")

    def _uploader_loop(self):
        """Luồng nền: lần lượt upload từng file lên PikPak bằng rclone."""
        while True:
            code = self.up_q.get()
            if code == "__backup__":
                self._do_backup()
                continue
            try:
                h = dict(self.history.get(code, {}))
                path = h.get("path", "")
                if not path or not os.path.isfile(path):
                    self.q.put(("upload", code, "error", "Không thấy file trên máy"))
                    continue
                r = find_rclone(self.cfg.get("rclone_path", ""))
                if not r:
                    self.q.put(("upload", code, "error", "Chưa có rclone.exe"))
                    continue
                fcfg = {f.get("name", ""): f for f in self.cfg["folders"]}
                dest = pikpak_dest(self.cfg, fcfg.get(h.get("folder", "")), h.get("folder", ""))
                self.q.put(("upload", code, "uploading", "↑  0%"))
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                p = subprocess.Popen([r, "--config", str(RCLONE_CONF), "copy", path, dest,
                                      "--retries", "3", "--low-level-retries", "10",
                                      "--stats", "2s", "--stats-one-line", "--stats-log-level", "NOTICE"],
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding="utf-8", errors="replace", creationflags=flags)
                last, tail = "", []
                for line in p.stdout:
                    tail = (tail + [line.strip()])[-5:]
                    m = re.search(r"(\d{1,3})%", line)
                    if m and m.group(1) != last:
                        last = m.group(1)
                        self.q.put(("upload", code, "uploading", f"↑  {last}%"))
                rc = p.wait()
                if rc == 0:
                    self.q.put(("upload", code, "done", dest))
                else:
                    self.q.put(("upload", code, "error", " | ".join(x for x in tail if x)[-200:]))
            except Exception as e:
                self.q.put(("upload", code, "error", str(e)))

    def _on_upload_msg(self, code, state, info):
        if state in ("done", "error"):
            self.up_queued.discard(code)
        h = self.history.get(code)
        if not h:
            return
        h["upload"] = state
        h["upload_msg"] = info
        if self.hist_tree.exists(code):
            self.hist_tree.set(code, "pikpak", self._upload_text(h))
        if state == "done":
            h["upload_ts"] = time.time()
            self.log(f"⇪ Đã upload lên PikPak: {h.get('name', '')}  →  {info}")
            self.notify("Đã upload lên PikPak", os.path.splitext(h.get("name", ""))[0])
            self.save_history()
        elif state == "error":
            self.log(f"⚠ Upload PikPak lỗi: {h.get('name', '')} — {info}")
            self.save_history()

    def _browse_lib(self):
        p = filedialog.askdirectory(title="Chọn thư mục phim")
        if p:
            self.lib_var.set(os.path.normpath(p))

    def _browse_ff(self):
        p = filedialog.askopenfilename(title="Chọn firefox.exe", filetypes=[("firefox.exe", "firefox.exe"), ("exe", "*.exe")])
        if p:
            self.ff_var.set(os.path.normpath(p))

    def _browse_watch(self):
        p = filedialog.askdirectory(title="Chọn thư mục IDM lưu phim")
        if p:
            cur = [x.strip() for x in self.watch_var.get().split(";") if x.strip()]
            p = os.path.normpath(p)
            if p not in cur:
                cur.append(p)
            self.watch_var.set("; ".join(cur))

    def _check_firefox(self):
        self.save_config()
        if not os.path.isfile(self.cfg.get("firefox_path", "")):
            messagebox.showwarning(APP_NAME, "Không tìm thấy firefox.exe. Chọn đường dẫn ở tab Cài đặt.")
            return False
        return True

    def save_config(self):
        if hasattr(self, "ff_var"):
            self.cfg["firefox_path"] = self.ff_var.get().strip()
            self.cfg["extensions"] = self.ext_var.get().strip()
            self.cfg["idm_autoclick"] = self.autoclick_var.get()
            self.cfg["watch_folders"] = self.watch_var.get().strip()
            self.cfg["close_firefox"] = self.close_ff_var.get()
            self.cfg["idm_window"] = self.idm_win_var.get()
            self.cfg["notify"] = self.notify_var.get()
            self.cfg["tray_on_close"] = self.tray_var.get()
            self.cfg["startup_scan"] = self.startscan_var.get()
            self.cfg["organize"] = self.organize_var.get()
            self.cfg["library_root"] = self.lib_var.get().strip()
            self.cfg["pikpak_on"] = self.pk_on_var.get()
            old_web = (self.cfg.get("web_on"), self.cfg.get("web_port"))
            self.cfg["web_on"] = self.web_on_var.get()
            try:
                self.cfg["web_port"] = min(65535, max(1024, int(self.web_port_var.get())))
            except (tk.TclError, ValueError):
                self.cfg["web_port"] = 8080
            self.cfg["web_pin"] = self.web_pin_var.get().strip()
            if old_web != (self.cfg["web_on"], self.cfg["web_port"]) and getattr(self, "web_srv", "init") != "init":
                self.after(100, self.start_web)
                if hasattr(self, "web_url_var"):
                    self.after(200, lambda: self.web_url_var.set("  ".join(self.web_urls())))
            self.cfg["backup_on"] = self.backup_on_var.get()
            self.cfg["pikpak_root"] = pikpak_join(self.pk_root_var.get())
            want = self.startwin_var.get()
            if want != self.cfg.get("start_with_windows") or want:
                try:
                    set_startup(want)   # luôn ghi lại đường dẫn mới nhất khi đang bật
                    self.cfg["start_with_windows"] = want
                except OSError as e:
                    self.log(f"Không đặt được khởi động cùng Windows: {e}")
            self.cfg["watch_ff_downloads"] = self.ffdl_var.get()
            for key, var, lo, dflt in (("delay", self.delay_var, 1, 4), ("idm_timeout", self.idm_to_var, 5, 25),
                                       ("ff_warmup", self.ff_warm_var, 0, 8)):
                try:
                    self.cfg[key] = max(lo, int(var.get()))
                except (tk.TclError, ValueError):
                    self.cfg[key] = dflt
        if hasattr(self, "auto_var"):
            self.cfg["auto_scan"] = self.auto_var.get()
            try:
                self.cfg["auto_interval"] = max(5, int(self.interval_var.get()))
            except (tk.TclError, ValueError):
                self.cfg["auto_interval"] = 60
        if getattr(self, "_no_save", False):
            return
        self._store_machine_profile()
        save_json(CONFIG_FILE, self.cfg)

    def _build_history(self):
        btns = ttk.Frame(self.tab_history, style="Card.TFrame")
        btns.pack(fill="x", pady=(0, 10))
        ttk.Button(btns, text="↻  Kiểm tra tải xong", style="Accent.TButton",
                   command=lambda: self._monitor_tick(manual=True)).pack(side="left")
        ttk.Button(btns, text="✔  Đánh dấu Xong", command=self.history_mark_done).pack(side="left", padx=8)
        ttk.Button(btns, text="⇪  Upload lên PikPak", command=self.history_upload_selected).pack(side="left", padx=(0, 8))
        ttk.Button(btns, text="↺  Xóa mục chọn (để tải lại)", command=self.history_delete).pack(side="left")
        ttk.Button(btns, text="✕  Xóa toàn bộ", style="Danger.TButton", command=self.history_clear).pack(side="left", padx=8)
        self.hist_count = tk.StringVar()
        ttk.Label(btns, textvariable=self.hist_count, style="Muted.TLabel").pack(side="right")
        flt = ttk.Frame(self.tab_history, style="Card.TFrame")
        flt.pack(fill="x", pady=(0, 8))
        ttk.Label(flt, text="⌕ Tìm", style="Muted.TLabel").pack(side="left")
        self.hist_search_var = tk.StringVar()
        ttk.Entry(flt, textvariable=self.hist_search_var, width=30).pack(side="left", padx=(6, 14))
        self.hist_search_var.trace_add("write", lambda *a: self.refresh_history())
        ttk.Label(flt, text="Trạng thái", style="Muted.TLabel").pack(side="left")
        self.hist_show_var = tk.StringVar(value="Tất cả")
        hc = ttk.Combobox(flt, textvariable=self.hist_show_var, state="readonly", width=14,
                          values=("Tất cả", "Đang tải", "Xong", "Lỗi", "Hôm nay", "Lỗi upload"))
        hc.pack(side="left", padx=6)
        hc.bind("<<ComboboxSelected>>", lambda e: self.refresh_history())
        self.hist_sort = ("sent", True)
        wrap, self.hist_tree = self._tree(self.tab_history, ("folder", "name", "sent", "status", "pikpak"),
                                          ("THƯ MỤC", "TÊN FILE", "THỜI GIAN", "TRẠNG THÁI", "PIKPAK"),
                                          (190, 420, 130, 140, 150), stretch=("folder", "name"))
        wrap.pack(fill="both", expand=True)
        for c in ("folder", "name", "sent", "status"):
            self.hist_tree.heading(c, command=lambda c=c: self._sort_history(c))
        self.hist_tree.bind("<Double-1>", self._open_history_file)
        ttk.Label(self.tab_history, text="Nhấp đúp 1 tập đã tải xong để mở thư mục chứa file.",
                  style="Muted.TLabel").pack(anchor="w", pady=(6, 0))
        self.refresh_history()

    @staticmethod
    def _status_tag(status):
        if status == STATUS_DONE:
            return "done"
        if status == STATUS_FAIL:
            return "err"
        if status in WATCH_STATUSES:
            return "dl"
        return "err" if status.startswith("Lỗi") else ""

    @staticmethod
    def _status_text(status):
        return {STATUS_DONE: "✔  Xong", STATUS_DL: "⬇  Đang tải", STATUS_NEW: "●  Tập mới"}.get(status, status)

    def _sort_history(self, col):
        cur, rev = self.hist_sort
        self.hist_sort = (col, (not rev) if cur == col else (col == "sent"))
        self.refresh_history()

    def refresh_history(self):
        self.hist_tree.delete(*self.hist_tree.get_children())
        col, rev = getattr(self, "hist_sort", ("sent", True))
        keyf = {"folder": lambda kv: strip_accents(kv[1].get("folder", "")),
                "name": lambda kv: strip_accents(kv[1].get("name", "")),
                "status": lambda kv: kv[1].get("status", ""),
                "sent": lambda kv: kv[1].get("ts", 0) or 0}[col]
        items = sorted(self.history.items(), key=keyf, reverse=rev)
        q = strip_accents(self.hist_search_var.get().strip()) if hasattr(self, "hist_search_var") else ""
        show = self.hist_show_var.get() if hasattr(self, "hist_show_var") else "Tất cả"
        today = dt.date.today()

        def keep(h):
            st = h.get("status", "")
            if q and q not in strip_accents(f"{h.get('folder', '')} {h.get('name', '')}"):
                return False
            if show == "Đang tải":
                return st in WATCH_STATUSES
            if show == "Xong":
                return st == STATUS_DONE
            if show == "Lỗi":
                return st == STATUS_FAIL or st.startswith("Lỗi")
            if show == "Lỗi upload":
                return h.get("upload") == "error"
            if show == "Hôm nay":
                try:
                    return dt.datetime.fromtimestamp(h.get("ts", 0) or 0).date() == today
                except (OSError, ValueError, OverflowError):
                    return False
            return True
        items = [kv for kv in items if keep(kv[1])]
        heads = {"folder": "THƯ MỤC", "name": "TÊN TẬP", "sent": "THỜI GIAN", "status": "TRẠNG THÁI"}
        for c, t in heads.items():
            self.hist_tree.heading(c, text=t + ((" ▼" if rev else " ▲") if c == col else ""))
        for i, (code, h) in enumerate(items):
            st = h.get("status", "")
            tags = [t for t in (("odd" if i % 2 else ""), self._status_tag(st)) if t]
            self.hist_tree.insert("", "end", iid=code, values=(h.get("folder", ""), h.get("name", ""),
                                                               h.get("sent_at", ""), self._status_text(st),
                                                               self._upload_text(h)), tags=tags)
        self.hist_count.set(f"Hiện {len(items)}/{len(self.history)} tập")
        if hasattr(self, "stat_vars"):
            self.update_stats()

    def _open_history_file(self, _e=None):
        sel = self.hist_tree.selection()
        if not sel:
            return
        path = self.history.get(sel[0], {}).get("path")
        if path and os.path.exists(path) and os.name == "nt":
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif path and os.path.exists(path):
            self.status_var.set(path)
        else:
            self.status_var.set("Chưa có đường dẫn file (tập chưa tải xong hoặc đã bị chuyển/xóa).")

    def save_history(self):
        save_json(HISTORY_FILE, self.history)

    def history_delete(self):
        sel = self.hist_tree.selection()
        if not sel or not messagebox.askyesno(APP_NAME, f"Xóa {len(sel)} tập khỏi lịch sử? Lần quét sau sẽ tải lại."):
            return
        for code in sel:
            self.history.pop(code, None)
        self.save_history(); self.refresh_history()

    def history_clear(self):
        if messagebox.askyesno(APP_NAME, "Xóa toàn bộ lịch sử đã tải?"):
            self.history = {}
            self.save_history(); self.refresh_history()

    @staticmethod
    def _names_preview(names, n=3):
        out = ", ".join(os.path.splitext(x)[0] for x in names[:n])
        return out + (f" và {len(names) - n} tập khác" if len(names) > n else "")

    def _start_tray(self):
        if not TRAY_OK:
            return
        try:
            import base64, io
            img = PILImage.open(io.BytesIO(base64.b64decode(ICON64)))
            put = lambda what: (lambda: self.q.put(("tray", what)))
            menu = pystray.Menu(
                pystray.MenuItem("Mở Fshare Auto Download", put("show"), default=True),
                pystray.MenuItem("Quét & Tải ngay", put("scan")),
                pystray.MenuItem(lambda item: "Tắt tự quét" if self.cfg.get("auto_scan") else "Bật tự quét",
                                 put("toggle_auto")),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Thoát", put("quit")))
            self.tray = pystray.Icon("FshareAutoDownload", img, APP_NAME, menu)
            threading.Thread(target=self.tray.run, daemon=True).start()
        except Exception as e:
            self.tray = None
            self.log(f"Không tạo được biểu tượng khay: {e}")

    def notify(self, title, msg):
        self.log(f"» {title}: {msg}")
        if self.tray and self.cfg.get("notify", True):
            try:
                self.tray.notify(msg, title)
            except Exception:
                pass

    def _restore_window(self):
        w = self.cfg.get("window") or {}
        if re.fullmatch(r"\d+x\d+[+-]-?\d+[+-]-?\d+", str(w.get("geometry", ""))):
            self.geometry(w["geometry"])
            self._normal_geom = w["geometry"]
        if w.get("zoomed"):
            self.after(50, lambda: self.state("zoomed"))

    def _track_geom(self, e):
        if e.widget is self and self.state() == "normal":
            self._normal_geom = self.geometry()

    def _init_col_memory(self):
        """Nhớ độ rộng cột các bảng (kéo vạch giữa tiêu đề cột) - lưu khi thả chuột và khi tắt tool."""
        self._col_trees = {"scan": self.scan_tree, "hist": self.hist_tree, "fp": self.fp_tree}
        saved = self.cfg.get("col_w") or {}
        for key, tree in self._col_trees.items():
            widths = saved.get(key) or {}
            for c in tree["columns"]:
                if c in widths:
                    try:
                        tree.column(c, width=int(widths[c]), stretch=False)
                    except (tk.TclError, ValueError):
                        pass
            if widths:   # cột cuối giãn theo cửa sổ để không bị khoảng trống
                tree.column(tree["columns"][-1], stretch=True)
            tree.bind("<ButtonRelease-1>", lambda e, t=tree: self._col_released(e, t), add="+")

    def _col_released(self, e, tree):
        if tree.identify_region(e.x, e.y) in ("separator", "heading"):
            self._save_cols()
            save_json(CONFIG_FILE, self.cfg) if not self._no_save else None

    def _save_cols(self):
        if not getattr(self, "_col_trees", None):
            return
        out = {}
        for key, tree in self._col_trees.items():
            try:
                out[key] = {c: int(tree.column(c, "width")) for c in tree["columns"]}
            except tk.TclError:
                pass
        self.cfg["col_w"] = out

    def _remember_window(self):
        self._save_cols()
        st = self.state()
        if st in ("normal", "zoomed"):
            self._was_zoomed = st == "zoomed"
        self.cfg["window"] = {"geometry": self._normal_geom or self.geometry(), "zoomed": self._was_zoomed}

    def show_window(self):
        if self.state() == "withdrawn":
            self.deiconify()
            if self._was_zoomed:
                self.state("zoomed")
        self.bring_to_front()

    def on_close(self):
        if self.tray and self.cfg.get("tray_on_close", True):
            self._remember_window()
            self.save_config()
            self.withdraw()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.notify(APP_TITLE, "Tool vẫn chạy dưới khay hệ thống. Nhấp đúp biểu tượng để mở lại.")
            return
        self.quit_app()

    def quit_app(self, force=False):
        if self.worker and self.worker.is_alive() and not force:
            if not messagebox.askyesno(APP_NAME, "Tool đang quét/tải. Vẫn thoát?"):
                return
            self.stop_flag.set()
        if self.state() != "withdrawn" and not self._no_save:
            self._remember_window()
        if not self._no_save:
            self.save_config()
        self.stop_web()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        self.destroy()

    def _auto_changed(self):
        self.save_config()
        if self.cfg.get("auto_scan"):
            self.next_auto_ts = time.time() + int(self.cfg.get("auto_interval", 60)) * 60
            self.log(f"Bật tự quét mỗi {self.cfg['auto_interval']} phút.")
        else:
            self.next_auto_ts = None
            self.log("Tắt tự quét.")
        self._update_auto_label()
        if self.tray:
            try:
                self.tray.update_menu()
            except Exception:
                pass

    def _update_auto_label(self):
        if self.next_auto_ts:
            txt = f"⏱  Quét lần tới: {dt.datetime.fromtimestamp(self.next_auto_ts):%H:%M}"
        else:
            txt = "⏱  Tự quét: tắt"
        self.header_auto_text = txt
        try:
            self.top_auto.config(text=txt)
            self.side_auto.config(text=txt.replace("⏱  ", "⏱ "))
        except (tk.TclError, AttributeError):
            pass

    def _auto_tick(self):
        self.after(15000, self._auto_tick)
        now = time.time()
        if self.cfg.get("auto_scan") and not self.next_auto_ts:
            self.next_auto_ts = now + int(self.cfg.get("auto_interval", 60)) * 60
        if self.next_auto_ts and now >= self.next_auto_ts:
            if self.worker and self.worker.is_alive():
                self.next_auto_ts = now + 120          # đang bận -> thử lại sau 2 phút
            else:
                self.log("⏱ Tự quét theo lịch...")
                self.start_scan(True, auto=True)
                self.next_auto_ts = (now + int(self.cfg.get("auto_interval", 60)) * 60
                                     if self.cfg.get("auto_scan") else None)
        self._update_auto_label()

    def bring_to_front(self):
        """Đưa tool lên trước, GIỮ NGUYÊN kích thước (không làm mất trạng thái phóng to)."""
        try:
            if os.name == "nt":
                import ctypes
                u32 = ctypes.windll.user32
                hwnd = u32.GetParent(self.winfo_id()) or self.winfo_id()
                if u32.IsIconic(hwnd):
                    u32.ShowWindow(hwnd, 9)  # SW_RESTORE: chỉ khi đang thu nhỏ -> về đúng kích thước cũ
                self.lift()
                u32.keybd_event(0x12, 0, 0, 0)  # nhấn ALT để Windows cho phép đưa cửa sổ lên trước
                u32.SetForegroundWindow(hwnd)
                u32.keybd_event(0x12, 0, 2, 0)
            else:
                if self.state() == "iconic":
                    self.deiconify()
                self.lift()
            self.focus_force()
        except Exception:
            pass

if __name__ == "__main__":
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DuyAnh.FshareAutoDownload")
        except Exception:
            pass
    if "--after-update" in sys.argv:
        time.sleep(2.5)           # chờ bản cũ thoát hẳn (nhả cổng web)
    if not run_newer_script():
        App(auto="--auto" in sys.argv, startup="--startup" in sys.argv).mainloop()
