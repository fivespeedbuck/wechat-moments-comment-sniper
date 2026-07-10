import ctypes
import os
import queue
import statistics
import subprocess
import sys
import threading
import time
import traceback
from ctypes import wintypes
from datetime import datetime

if getattr(sys, "frozen", False):
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    os.environ.setdefault("TCL_LIBRARY", os.path.join(base_dir, "tcl", "tcl8.6"))
    os.environ.setdefault("TK_LIBRARY", os.path.join(base_dir, "tcl", "tk8.6"))

import tkinter as tk
from tkinter import ttk

try:
    from PIL import ImageChops, ImageGrab, ImageStat
except Exception as exc:
    ImageGrab = None
    PIL_IMPORT_ERROR = exc
else:
    PIL_IMPORT_ERROR = None


user32 = ctypes.WinDLL("user32", use_last_error=True)
winmm = ctypes.WinDLL("winmm", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

VK_CONTROL = 0x11
VK_V = 0x56
VK_RETURN = 0x0D
VK_F7 = 0x76
VK_F8 = 0x77
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
SW_RESTORE = 9
HIGH_PRIORITY_CLASS = 0x00000080
THREAD_PRIORITY_HIGHEST = 2
SRCCOPY = 0x00CC0020
BI_RGB = 0


def enable_dpi_awareness():
    try:
        # Per-monitor v2 when available.
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


def set_timer_res(ms=1):
    try:
        winmm.timeBeginPeriod(ms)
    except Exception:
        pass


def reset_timer_res(ms=1):
    try:
        winmm.timeEndPeriod(ms)
    except Exception:
        pass


def now_str():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def get_cursor_pos():
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def is_f8_pressed():
    return bool(user32.GetAsyncKeyState(VK_F8) & 0x8000)


def is_f7_pressed():
    return bool(user32.GetAsyncKeyState(VK_F7) & 0x8000)


def set_cursor_pos(x, y):
    user32.SetCursorPos(int(x), int(y))


def left_click(x, y):
    set_cursor_pos(x, y)
    time.sleep(0.012)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def key_down(vk):
    user32.keybd_event(vk, 0, 0, 0)


def key_up(vk):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def press_enter():
    key_down(VK_RETURN)
    key_up(VK_RETURN)


def paste_text(text, root):
    root.clipboard_clear()
    root.clipboard_append(text)
    root.update()
    time.sleep(0.02)
    key_down(VK_CONTROL)
    key_down(VK_V)
    key_up(VK_V)
    key_up(VK_CONTROL)


def find_wechat_window():
    return (
        user32.FindWindowW("WeChatMainWndForPC", None)
        or user32.FindWindowW(None, "微信")
        or user32.FindWindowW(None, "Weixin")
    )


def bring_wechat_front():
    hwnd = find_wechat_window()
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetForegroundWindow(hwnd)
    return True


def grab_region_win32(region):
    x, y, w, h = region
    w = int(w)
    h = int(h)
    if w <= 0 or h <= 0:
        raise RuntimeError("截图区域宽高必须大于 0")

    screen_dc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    bmp = gdi32.CreateCompatibleBitmap(screen_dc, w, h)
    old_obj = gdi32.SelectObject(mem_dc, bmp)
    try:
        if not gdi32.BitBlt(mem_dc, 0, 0, w, h, screen_dc, int(x), int(y), SRCCOPY):
            raise ctypes.WinError(ctypes.get_last_error())

        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        rows = gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
        if rows != h:
            raise ctypes.WinError(ctypes.get_last_error())
        return {"kind": "raw", "w": w, "h": h, "data": bytes(buf)}
    finally:
        gdi32.SelectObject(mem_dc, old_obj)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(None, screen_dc)


def raw_pixel(img, x, y):
    idx = (y * img["w"] + x) * 4
    b = img["data"][idx]
    g = img["data"][idx + 1]
    r = img["data"][idx + 2]
    return r, g, b


def raw_gray(img, x, y):
    r, g, b = raw_pixel(img, x, y)
    return (r * 30 + g * 59 + b * 11) / 100.0


def img_size(img):
    if isinstance(img, dict):
        return img["w"], img["h"]
    return img.size


def gray_at(img, x, y):
    if isinstance(img, dict):
        return raw_gray(img, x, y)
    return img.getpixel((x, y))


def screen_region(region):
    if ImageGrab is None:
        return grab_region_win32(region)
    x, y, w, h = region
    return ImageGrab.grab(bbox=(x, y, x + w, y + h)).convert("L")


def screen_template_around(cx, cy, w=64, h=40):
    x = max(0, int(cx - w / 2))
    y = max(0, int(cy - h / 2))
    return screen_region((x, y, w, h))


def locate_template_center(template, region, min_x_ratio=0.68):
    img = screen_region(region)
    sw, sh = img_size(img)
    tw, th = img_size(template)
    if sw < tw or sh < th:
        return None, None

    best_score = None
    best_pos = None
    scan_step = 6
    sample_step = 8
    start_x = max(0, int(sw * min_x_ratio))

    sample_points = [
        (tx, ty)
        for ty in range(0, th, sample_step)
        for tx in range(0, tw, sample_step)
    ]
    if not sample_points:
        return None, None

    for y in range(0, sh - th + 1, scan_step):
        for x in range(start_x, sw - tw + 1, scan_step):
            total = 0.0
            for tx, ty in sample_points:
                total += abs(gray_at(template, tx, ty) - gray_at(img, x + tx, y + ty))
            score = total / len(sample_points)
            if best_score is None or score < best_score:
                best_score = score
                best_pos = (region[0] + x + tw // 2, region[1] + y + th // 2)

    if best_score is not None and best_score <= 28:
        return best_pos, best_score
    return None, best_score


def locate_template_center_fixed_x(template, region, fixed_center_x, tolerance=28):
    img = screen_region(region)
    sw, sh = img_size(img)
    tw, th = img_size(template)
    if sw < tw or sh < th:
        return None, None

    local_center_x = int(fixed_center_x - region[0])
    x_min = max(0, local_center_x - tw // 2 - tolerance)
    x_max = min(sw - tw, local_center_x - tw // 2 + tolerance)
    if x_min > x_max:
        return None, None

    best_score = None
    best_pos = None
    scan_y_step = 4
    scan_x_step = 4
    sample_step = 8
    sample_points = [
        (tx, ty)
        for ty in range(0, th, sample_step)
        for tx in range(0, tw, sample_step)
    ]

    for y in range(0, sh - th + 1, scan_y_step):
        for x in range(x_min, x_max + 1, scan_x_step):
            total = 0.0
            for tx, ty in sample_points:
                total += abs(gray_at(template, tx, ty) - gray_at(img, x + tx, y + ty))
            score = total / len(sample_points)
            if best_score is None or score < best_score:
                best_score = score
                best_pos = (region[0] + x + tw // 2, region[1] + y + th // 2)

    if best_score is not None and best_score <= 28:
        return best_pos, best_score
    return None, best_score


def image_change_score(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        if a["w"] != b["w"] or a["h"] != b["h"]:
            return 255.0
        total = 0.0
        step = 2 if a["w"] * a["h"] > 160000 else 1
        count = 0
        for y in range(0, a["h"], step):
            for x in range(0, a["w"], step):
                total += abs(raw_gray(a, x, y) - raw_gray(b, x, y))
                count += 1
        return total / max(1, count)

    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    return stat.mean[0]


def locate_moments_more_button(region):
    img = screen_region(region)
    if not isinstance(img, dict):
        img = img.convert("RGB")
    rx, ry, rw, rh = region
    dots = []
    x_start = int(rw * 0.50)
    for y in range(40, rh - 4):
        for x in range(x_start, rw - 4):
            if isinstance(img, dict):
                r, g, b = raw_pixel(img, x, y)
            else:
                r, g, b = img.getpixel((x, y))
            blue_gray_dot = 35 <= r <= 175 and 45 <= g <= 180 and 55 <= b <= 205 and b >= r + 3
            dark_dot = 20 <= r <= 120 and 20 <= g <= 130 and 20 <= b <= 150
            if blue_gray_dot or dark_dot:
                dots.append((x, y))

    if not dots:
        return None

    # Collapse nearby blue-gray pixels into small dot centers.
    remaining = set(dots)
    comps = []
    while remaining:
        start = remaining.pop()
        stack = [start]
        xs = [start[0]]
        ys = [start[1]]
        while stack:
            x, y = stack.pop()
            for nx in (x - 1, x, x + 1):
                for ny in (y - 1, y, y + 1):
                    if (nx, ny) in remaining:
                        remaining.remove((nx, ny))
                        stack.append((nx, ny))
                        xs.append(nx)
                        ys.append(ny)
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        if 2 <= w <= 18 and 2 <= h <= 18:
            comps.append((sum(xs) / len(xs), sum(ys) / len(ys), min(xs), min(ys), max(xs), max(ys)))

    pairs = []
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            dy = abs(a[1] - b[1])
            dx = abs(a[0] - b[0])
            if dy <= 7 and 5 <= dx <= 26:
                cx = (a[0] + b[0]) / 2
                cy = (a[1] + b[1]) / 2
                if cy > 35 and cx >= rw * 0.62:
                    pairs.append((cx, cy))

    if not pairs:
        return None

    # Moments "..." buttons sit on the right edge of each post. Prefer the
    # rightmost candidate, then the lower one when there are multiple posts.
    cx, cy = max(pairs, key=lambda p: (p[0], p[1]))
    return int(rx + cx), int(ry + cy)


def locate_moments_more_button_fixed_x(region, fixed_x, tolerance=46):
    img = screen_region(region)
    if not isinstance(img, dict):
        img = img.convert("RGB")
    rx, ry, rw, rh = region
    local_x = int(fixed_x - rx)
    x_min = max(0, local_x - tolerance)
    x_max = min(rw - 1, local_x + tolerance)
    if x_min >= x_max:
        return None

    def px(x, y):
        if isinstance(img, dict):
            return raw_pixel(img, x, y)
        return img.getpixel((x, y))

    def is_dot_color(r, g, b):
        blue_gray_dot = 35 <= r <= 175 and 45 <= g <= 180 and 55 <= b <= 205 and b >= r + 3
        dark_dot = 20 <= r <= 120 and 20 <= g <= 130 and 20 <= b <= 150
        return blue_gray_dot or dark_dot

    def is_button_bg(r, g, b):
        return abs(r - g) <= 10 and abs(g - b) <= 10 and 228 <= r <= 248

    dots = []
    for y in range(20, rh - 4):
        for x in range(x_min, x_max + 1):
            r, g, b = px(x, y)
            if is_dot_color(r, g, b):
                dots.append((x, y))

    if not dots:
        return None

    remaining = set(dots)
    comps = []
    while remaining:
        start = remaining.pop()
        stack = [start]
        xs = [start[0]]
        ys = [start[1]]
        while stack:
            x, y = stack.pop()
            for nx in (x - 1, x, x + 1):
                for ny in (y - 1, y, y + 1):
                    if (nx, ny) in remaining:
                        remaining.remove((nx, ny))
                        stack.append((nx, ny))
                        xs.append(nx)
                        ys.append(ny)
        w = max(xs) - min(xs) + 1
        h = max(ys) - min(ys) + 1
        if 2 <= w <= 18 and 2 <= h <= 18:
            comps.append((sum(xs) / len(xs), sum(ys) / len(ys)))

    def bg_score(cx, cy):
        score = 0
        for yy in range(int(cy) - 10, int(cy) + 11, 2):
            if yy < 0 or yy >= rh:
                continue
            for xx in range(int(cx) - 24, int(cx) + 25, 3):
                if xx < 0 or xx >= rw:
                    continue
                r, g, b = px(xx, yy)
                if is_button_bg(r, g, b):
                    score += 1
        return score

    candidates = []
    for i, a in enumerate(comps):
        for b in comps[i + 1 :]:
            dy = abs(a[1] - b[1])
            dx = abs(a[0] - b[0])
            if dy <= 7 and 5 <= dx <= 30:
                cx = (a[0] + b[0]) / 2
                cy = (a[1] + b[1]) / 2
                x_error = abs((rx + cx) - fixed_x)
                if x_error <= tolerance:
                    candidates.append((cy, x_error, bg_score(cx, cy), int(fixed_x), int(ry + cy)))

    if not candidates:
        return None

    with_bg = [c for c in candidates if c[2] >= 18]
    chosen = min(with_bg or candidates, key=lambda item: (item[0], item[1]))
    _, _, _, x, y = chosen
    return x, y


def is_send_green_color(r, g, b):
    return (
        0 <= r <= 95
        and 120 <= g <= 240
        and 0 <= b <= 180
        and g >= r + 55
        and g >= b + 25
    )


def locate_green_send_button(min_y=0):
    if ImageGrab is None:
        img = grab_region_win32((0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))
        w, h = img["w"], img["h"]
        get_pixel = lambda px, py: raw_pixel(img, px, py)
    else:
        img = ImageGrab.grab().convert("RGB")
        w, h = img.size
        get_pixel = lambda px, py: img.getpixel((px, py))

    boxes = []
    active = []

    for y in range(max(0, int(min_y)), h, 2):
        runs = []
        x = 0
        while x < w:
            r, g, b = get_pixel(x, y)
            if is_send_green_color(r, g, b):
                x1 = x
                while x < w:
                    r, g, b = get_pixel(x, y)
                    if not is_send_green_color(r, g, b):
                        break
                    x += 1
                x2 = x
                if 35 <= x2 - x1 <= 140:
                    runs.append((x1, x2))
            x += 1

        new_active = []
        used = [False] * len(runs)
        for box in active:
            bx1, by1, bx2, by2 = box
            matched = False
            for i, (x1, x2) in enumerate(runs):
                if used[i]:
                    continue
                if max(bx1, x1) <= min(bx2, x2):
                    new_active.append((min(bx1, x1), by1, max(bx2, x2), y))
                    used[i] = True
                    matched = True
                    break
            if not matched:
                boxes.append(box)
        for i, (x1, x2) in enumerate(runs):
            if not used[i]:
                new_active.append((x1, y, x2, y))
        active = new_active
    boxes.extend(active)

    valid = []
    for x1, y1, x2, y2 in boxes:
        bw = x2 - x1
        bh = y2 - y1 + 2
        if 45 <= bw <= 140 and 18 <= bh <= 70:
            valid.append((bw * bh, x1, y1, x2, y2))

    if not valid:
        return None
    _, x1, y1, x2, y2 = max(valid)
    return int((x1 + x2) / 2), int((y1 + y2) / 2)


def wait_green_send_button(min_y=0, timeout=0.35, interval=0.02, cancel_ev=None):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end and not (cancel_ev and cancel_ev.is_set()):
        pos = locate_green_send_button(min_y=min_y)
        if pos:
            return pos
        time.sleep(interval)
    return None


def locate_green_send_button_near(pred_x, pred_y, range_x=170, range_y=300):
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    left = max(0, int(pred_x - range_x))
    top = max(0, int(pred_y - range_y))
    right = min(screen_w, int(pred_x + range_x))
    bottom = min(screen_h, int(pred_y + range_y))
    if right <= left or bottom <= top:
        return None

    img = grab_region_win32((left, top, right - left, bottom - top))
    w, h = img["w"], img["h"]

    boxes = []
    active = []
    for y in range(0, h, 2):
        runs = []
        x = 0
        while x < w:
            r, g, b = raw_pixel(img, x, y)
            if is_send_green_color(r, g, b):
                x1 = x
                while x < w:
                    r, g, b = raw_pixel(img, x, y)
                    if not is_send_green_color(r, g, b):
                        break
                    x += 1
                x2 = x
                if 25 <= x2 - x1 <= 170:
                    runs.append((x1, x2))
            x += 1

        new_active = []
        used = [False] * len(runs)
        for box in active:
            bx1, by1, bx2, by2 = box
            matched = False
            for i, (x1, x2) in enumerate(runs):
                if used[i]:
                    continue
                if max(bx1, x1) <= min(bx2, x2):
                    new_active.append((min(bx1, x1), by1, max(bx2, x2), y))
                    used[i] = True
                    matched = True
                    break
            if not matched:
                boxes.append(box)
        for i, (x1, x2) in enumerate(runs):
            if not used[i]:
                new_active.append((x1, y, x2, y))
        active = new_active
    boxes.extend(active)

    valid = []
    for x1, y1, x2, y2 in boxes:
        bw = x2 - x1
        bh = y2 - y1 + 2
        if 35 <= bw <= 170 and 16 <= bh <= 80:
            cx = left + (x1 + x2) / 2
            cy = top + (y1 + y2) / 2
            dist = abs(cx - pred_x) + abs(cy - pred_y) * 0.6
            valid.append((dist, bw * bh, int(cx), int(cy)))

    if not valid:
        return None
    _, _, cx, cy = min(valid, key=lambda item: (item[0], -item[1]))
    return cx, cy


def wait_green_send_button_near(pred_x, pred_y, timeout=1.2, interval=0.03, cancel_ev=None):
    end = time.perf_counter() + timeout
    while time.perf_counter() < end and not (cancel_ev and cancel_ev.is_set()):
        pos = locate_green_send_button_near(pred_x, pred_y)
        if pos:
            return pos
        time.sleep(interval)
    return None


def run_text(cmd):
    return subprocess.check_output(cmd, shell=True, stderr=subprocess.DEVNULL).decode(
        "gbk", "ignore"
    )


def find_wechat_endpoints():
    endpoints = []
    for proc_name in ("Weixin.exe", "WeChat.exe"):
        try:
            out = run_text(f'tasklist /FI "IMAGENAME eq {proc_name}" /NH')
        except Exception:
            continue
        pids = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].lower() == proc_name.lower():
                pids.append(parts[1])
        for pid in pids:
            try:
                net = run_text(f"netstat -ano | findstr {pid}")
            except Exception:
                continue
            for line in net.splitlines():
                parts = line.split()
                if len(parts) < 5 or parts[0].upper() != "TCP" or "ESTABLISHED" not in line:
                    continue
                remote = parts[2]
                if remote.startswith(("127.", "0.", "[::1]")) or remote.startswith("["):
                    continue
                endpoints.append(remote)
    return sorted(set(endpoints))


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("朋友圈留言抢车半自动版")
        self.root.geometry("980x680")
        self.root.minsize(880, 580)
        self.ui_q = queue.Queue()
        self.cancel_ev = threading.Event()
        self.worker_lock = threading.Lock()
        self.worker_running = False
        self.baseline = None
        self.more_template = None
        self.last_more_predicted_pos = None
        self.capture_mode = None
        self.f7_was_down = False
        self.f8_was_down = False
        self._build_ui()
        self.root.bind_all("<F7>", lambda _event: self.cancel("F7"))
        self._loop_ui()
        self._poll_f7_stop()
        self._poll_f8_capture()
        self._track_wechat()

    def _build_ui(self):
        main = ttk.Frame(self.root, padding=14)
        main.pack(fill="both", expand=True)

        header = ttk.Frame(main)
        header.pack(fill="x", pady=(0, 2))
        ttk.Label(header, text="◤ 朋友圈抢车大王 · STREET RUNNER ◢", style="Banner.TLabel").pack(side="left")
        ttk.Label(header, text="// AUTO COMMENT SNIPER", style="Sub.TLabel").pack(side="left", padx=10)
        tk.Frame(main, height=2, bg=CP_CYAN, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 10))

        self.target_v = tk.StringVar(value=datetime.now().strftime("%Y-%m-%d %H:%M:%S.000"))
        self.comment_v = tk.StringVar(value="我要车")
        self.refresh_ms_v = tk.StringVar(value="80")
        self.start_before_ms_v = tk.StringVar(value="500")
        self.threshold_v = tk.StringVar(value="8.0")
        self.after_detect_delay_ms_v = tk.StringVar(value="50")
        self.stable_wait_ms_v = tk.StringVar(value="200")
        self.stable_threshold_v = tk.StringVar(value="1.2")
        self.more_stable_wait_ms_v = tk.StringVar(value="200")
        self.more_stable_frames_v = tk.StringVar(value="2")
        self.more_stable_px_v = tk.StringVar(value="5")
        self.refresh_x_v = tk.StringVar(value="0")
        self.refresh_y_v = tk.StringVar(value="0")
        self.menu_dx_v = tk.StringVar(value="-48")
        self.menu_dy_v = tk.StringVar(value="0")
        self.send_dx_v = tk.StringVar(value="0")
        self.send_dy_v = tk.StringVar(value="0")
        self.more_dx_v = tk.StringVar(value="0")
        self.more_dy_v = tk.StringVar(value="0")
        self.region_x_v = tk.StringVar(value="0")
        self.region_y_v = tk.StringVar(value="0")
        self.region_w_v = tk.StringVar(value="700")
        self.region_h_v = tk.StringVar(value="650")
        self.last_more_pos = None
        self.send_offset_set = False
        self._adv_win = None

        cfg = ttk.LabelFrame(main, text="抢车设置", padding=12)
        cfg.pack(fill="x")

        c0 = ttk.Frame(cfg)
        c0.pack(fill="x", pady=4)
        ttk.Label(c0, text="目标时刻:", width=12).pack(side="left")
        ttk.Entry(c0, textvariable=self.target_v, width=26).pack(side="left", padx=5)
        ttk.Label(c0, text="格式 2026-05-29 12:00:00.000", style="Hint.TLabel").pack(side="left", padx=6)

        c1 = ttk.Frame(cfg)
        c1.pack(fill="x", pady=4)
        ttk.Label(c1, text="留言内容:", width=12).pack(side="left")
        ttk.Entry(c1, textvariable=self.comment_v, width=46).pack(side="left", padx=5)

        c2 = ttk.Frame(cfg)
        c2.pack(fill="x", pady=4)
        ttk.Label(c2, text="变化阈值:", width=12).pack(side="left")
        ttk.Entry(c2, textvariable=self.threshold_v, width=10).pack(side="left", padx=5)
        ttk.Label(c2, text="越小越敏感，建议 6-12（其余参数在高级设置）", style="Hint.TLabel").pack(side="left", padx=6)

        cap_f = ttk.LabelFrame(main, text="校准捕获（点按钮后按 F8 取点；每次抢车前重校）", padding=10)
        cap_f.pack(fill="x", pady=8)
        cap_btns = [
            ("F8取刷新点", lambda: self.begin_capture("refresh")),
            ("F8取检测区左上", lambda: self.begin_capture("region_tl")),
            ("F8取检测区右下", lambda: self.begin_capture("region_br")),
            ("F8取…按钮", lambda: self.begin_capture("more_button")),
        ]
        for col in range(len(cap_btns)):
            cap_f.columnconfigure(col, weight=1)
        for i, (text, cmd) in enumerate(cap_btns):
            ttk.Button(cap_f, text=text, command=cmd).grid(row=0, column=i, sticky="ew", padx=3, pady=2)

        off_f = ttk.LabelFrame(main, text="偏移捕获 / 测试", padding=10)
        off_f.pack(fill="x", pady=8)
        off_btns = [
            ("F8取评论菜单偏移", lambda: self.begin_capture("menu_offset")),
            ("F8取发送按钮偏移", lambda: self.begin_capture("send_offset")),
            ("截取检测区为基准", self.capture_baseline),
            ("测试寻找…按钮", self.test_more_button),
        ]
        for col in range(len(off_btns)):
            off_f.columnconfigure(col, weight=1)
        for i, (text, cmd) in enumerate(off_btns):
            ttk.Button(off_f, text=text, command=cmd).grid(row=0, column=i, sticky="ew", padx=3, pady=2)

        op = ttk.LabelFrame(main, text="操作", padding=10)
        op.pack(fill="x", pady=6)
        op_row = ttk.Frame(op)
        op_row.pack(fill="x")
        ttk.Button(op_row, text="置前微信", command=self.front).pack(side="left", padx=3)
        ttk.Button(op_row, text="测试检测变化", command=self.test_change).pack(side="left", padx=3)
        ttk.Button(op_row, text="测试留言动作", command=self.test_comment_action).pack(side="left", padx=3)
        ttk.Button(op_row, text="⚙ 高级设置", command=self.show_advanced_window).pack(side="left", padx=3)
        tk.Button(op_row, text="▶ 开始抢留言", command=self.arm, bg=CP_YELLOW, fg="#0a0e16",
                  activebackground="#fff740", activeforeground="#0a0e16", relief="flat", bd=0,
                  cursor="hand2", font=("Microsoft YaHei UI", 13, "bold"), padx=24, pady=7).pack(side="right", padx=8)
        ttk.Button(op_row, text="终止 / F7", command=self.cancel).pack(side="right", padx=3)

        status = ttk.LabelFrame(main, text="状态", padding=10)
        status.pack(fill="x", pady=6)
        self.status_v = tk.StringVar(value="状态：等待校准")
        self.wechat_v = tk.StringVar(value="微信链路：搜索中")
        ttk.Label(status, textvariable=self.status_v, font=("Microsoft YaHei UI", 10, "bold"), foreground=CP_YELLOW).pack(side="left", padx=10)
        ttk.Label(status, textvariable=self.wechat_v, font=("Consolas", 10), foreground=CP_TERM).pack(side="right", padx=10)

        logs = ttk.LabelFrame(main, text="日志", padding=5)
        logs.pack(fill="both", expand=True, pady=6)
        self.log_t = tk.Text(logs, bg=CP_INPUT, fg=CP_TERM, font=("Consolas", 10), height=12,
                             insertbackground=CP_CYAN, selectbackground=CP_MAG,
                             relief="flat", bd=0, padx=8, pady=6, highlightthickness=0)
        self.log_t.pack(fill="both", expand=True)
        self.log_t.config(state="disabled")

    def show_advanced_window(self):
        if getattr(self, "_adv_win", None) and self._adv_win.winfo_exists():
            self._adv_win.deiconify()
            self._adv_win.lift()
            self._adv_win.focus_force()
            return
        win = tk.Toplevel(self.root)
        self._adv_win = win
        win.title("高级设置 / 坐标校准")
        win.geometry("660x580")
        win.configure(bg=CP_BG)

        wrap = ttk.Frame(win, padding=14)
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="◤ 高级设置 ◢", style="Banner.TLabel").pack(anchor="w")
        tk.Frame(wrap, height=2, bg=CP_MAG, bd=0, highlightthickness=0).pack(fill="x", pady=(2, 10))

        par = ttk.LabelFrame(wrap, text="时序参数（一般用默认）", padding=8)
        par.pack(fill="x", pady=4)
        adv_params = [
            ("刷新间隔(ms):", self.refresh_ms_v, "80-150"),
            ("提前开刷(ms):", self.start_before_ms_v, "300-800"),
            ("识别后等待(ms):", self.after_detect_delay_ms_v, "渲染缓冲"),
            ("稳定等待(ms):", self.stable_wait_ms_v, "等界面稳定"),
            ("稳定阈值:", self.stable_threshold_v, "0.8-2.0"),
            ("目标稳定(ms):", self.more_stable_wait_ms_v, "等…坐标稳"),
            ("目标稳定帧:", self.more_stable_frames_v, "2-5"),
            ("目标容差(px):", self.more_stable_px_v, "3-6"),
        ]
        for i, (lab, var, hint) in enumerate(adv_params):
            r, c = i // 2, (i % 2) * 3
            ttk.Label(par, text=lab, width=14, anchor="e").grid(row=r, column=c, sticky="e", padx=(6, 2), pady=4)
            ttk.Entry(par, textvariable=var, width=8).grid(row=r, column=c + 1, sticky="w", pady=4)
            ttk.Label(par, text=hint, style="Hint.TLabel").grid(row=r, column=c + 2, sticky="w", padx=(4, 10))

        pos = ttk.LabelFrame(wrap, text="坐标校准（F8 自动填充，一般不用手改）", padding=8)
        pos.pack(fill="x", pady=4)
        labels = [
            ("刷新点 X", self.refresh_x_v), ("刷新点 Y", self.refresh_y_v),
            ("…修正dx", self.more_dx_v), ("…修正dy", self.more_dy_v),
            ("评论菜单dx", self.menu_dx_v), ("评论菜单dy", self.menu_dy_v),
            ("发送按钮dx", self.send_dx_v), ("发送按钮dy", self.send_dy_v),
            ("检测区 X", self.region_x_v), ("检测区 Y", self.region_y_v),
            ("检测区 W", self.region_w_v), ("检测区 H", self.region_h_v),
        ]
        for i, (label, var) in enumerate(labels):
            ttk.Label(pos, text=label, anchor="e").grid(row=i // 4, column=(i % 4) * 2, sticky="e", padx=4, pady=4)
            ttk.Entry(pos, textvariable=var, width=9).grid(row=i // 4, column=(i % 4) * 2 + 1, sticky="w", padx=4, pady=4)

        ttk.Button(wrap, text="关闭", command=win.destroy).pack(anchor="e", pady=10)

    def region(self):
        return (
            int(self.region_x_v.get()),
            int(self.region_y_v.get()),
            int(self.region_w_v.get()),
            int(self.region_h_v.get()),
        )

    def append_log(self, msg):
        self.ui_q.put(("log", f"[{now_str()}] {msg}"))

    def _loop_ui(self):
        while not self.ui_q.empty():
            t, d = self.ui_q.get_nowait()
            if t == "log":
                self.log_t.config(state="normal")
                self.log_t.insert("end", d + "\n")
                self.log_t.see("end")
                self.log_t.config(state="disabled")
            elif t == "status":
                self.status_v.set(f"状态：{d}")
            elif t == "wechat":
                self.wechat_v.set(d)
        self.root.after(50, self._loop_ui)

    def _track_wechat(self):
        def task():
            while True:
                eps = find_wechat_endpoints()
                if eps:
                    self.ui_q.put(("wechat", f"微信链路：{' / '.join(eps[:2])}"))
                else:
                    self.ui_q.put(("wechat", "微信链路：未检测到"))
                time.sleep(3)

        threading.Thread(target=task, daemon=True).start()

    def front(self):
        ok = bring_wechat_front()
        self.append_log("已尝试置前微信。" if ok else "未找到微信窗口。")

    def begin_capture(self, mode):
        if mode == "menu_offset" and not self.last_more_pos:
            self.append_log("请先点“测试寻找…按钮”，再打开菜单，最后用 F8 取评论菜单偏移。")
            return True
        if mode == "send_offset" and not (self.last_more_predicted_pos or self.last_more_pos):
            self.append_log("请先点“测试寻找…按钮”，再打开评论框，最后用 F8 取发送按钮偏移。")
            return
        self.capture_mode = mode
        self.f8_was_down = is_f8_pressed()
        hints = {
            "refresh": "请把鼠标移到朋友圈刷新位置，然后按 F8。",
            "region_tl": "请把鼠标移到检测区左上角，然后按 F8。",
            "region_br": "请把鼠标移到检测区右下角，然后按 F8。",
            "more_button": "请把鼠标移到朋友圈右下角“…”按钮中心，然后按 F8。",
            "menu_offset": "请把鼠标移到弹出菜单里的“评论”位置，然后按 F8。",
            "send_offset": "请把鼠标移到绿色“发送”按钮中心，然后按 F8。",
        }
        self.append_log(hints.get(mode, "请移动鼠标后按 F8。"))
        self.ui_q.put(("status", "等待 F8 取点"))

    def _poll_f8_capture(self):
        pressed = is_f8_pressed()
        if self.capture_mode and pressed and not self.f8_was_down:
            self.finish_capture()
        self.f8_was_down = pressed
        self.root.after(30, self._poll_f8_capture)

    def _poll_f7_stop(self):
        pressed = is_f7_pressed()
        if pressed and not self.f7_was_down:
            self.cancel("F7")
        self.f7_was_down = pressed
        self.root.after(25, self._poll_f7_stop)

    def finish_capture(self):
        mode = self.capture_mode
        self.capture_mode = None
        x, y = get_cursor_pos()

        if mode == "refresh":
            self.refresh_x_v.set(str(x))
            self.refresh_y_v.set(str(y))
            self.append_log(f"刷新点已设置为 {x},{y}")
        elif mode == "region_tl":
            self.region_x_v.set(str(x))
            self.region_y_v.set(str(y))
            self.append_log(f"检测区左上已设置为 {x},{y}")
        elif mode == "region_br":
            x1 = int(self.region_x_v.get())
            y1 = int(self.region_y_v.get())
            left = min(x1, x)
            top = min(y1, y)
            right = max(x1, x)
            bottom = max(y1, y)
            self.region_x_v.set(str(left))
            self.region_y_v.set(str(top))
            self.region_w_v.set(str(max(1, right - left)))
            self.region_h_v.set(str(max(1, bottom - top)))
            self.append_log(f"检测区已设置为 x={left}, y={top}, w={right-left}, h={bottom-top}")
        elif mode == "more_button":
            self.more_template = screen_template_around(x, y)
            raw_fixed = locate_moments_more_button_fixed_x(self.region(), x)
            if raw_fixed:
                self.more_dx_v.set("0")
                self.more_dy_v.set(str(y - raw_fixed[1]))
                self.last_more_pos = (x, y)
                self.append_log(
                    f"朋友圈“…”固定X已校准: 原始Y {raw_fixed[1]} -> 实际Y {y} | dy={y - raw_fixed[1]}"
                )
            else:
                detected = locate_moments_more_button(self.region())
                if detected:
                    self.more_dx_v.set(str(x - detected[0]))
                    self.more_dy_v.set(str(y - detected[1]))
                    self.last_more_pos = (x, y)
                    self.append_log(
                        f"朋友圈“…”模板已捕获，并设置修正: 原始 {detected[0]},{detected[1]} -> 实际 {x},{y} | dx={x-detected[0]}, dy={y-detected[1]}"
                    )
                else:
                    self.last_more_pos = (x, y)
                    self.append_log(f"朋友圈“…”模板已捕获，中心位置 {x},{y}")
        elif mode == "menu_offset":
            mx, my = self.last_more_pos
            self.menu_dx_v.set(str(x - mx))
            self.menu_dy_v.set(str(y - my))
            self.append_log(f"评论菜单偏移已设置为 dx={x - mx}, dy={y - my}")
        elif mode == "send_offset":
            base = self.last_more_predicted_pos or self.last_more_pos
            mx, my = base
            self.send_dx_v.set(str(x - mx))
            self.send_dy_v.set(str(y - my))
            self.send_offset_set = True
            self.append_log(f"发送按钮偏移已设置为 dx={x - mx}, dy={y - my}")

        self.ui_q.put(("status", "取点完成"))

    def capture_baseline(self):
        try:
            self.baseline = screen_region(self.region())
            self.append_log("已截取当前检测区作为基准。")
        except Exception as exc:
            self.append_log(f"截取基准失败: {exc}")

    def test_change(self):
        try:
            if self.baseline is None:
                self.capture_baseline()
                return
            score = image_change_score(self.baseline, screen_region(self.region()))
            self.append_log(f"当前检测区变化分数: {score:.2f}")
        except Exception as exc:
            self.append_log(f"测试变化失败: {exc}")

    def wait_region_stable(self, timeout_ms, threshold):
        end = time.perf_counter() + max(0, timeout_ms) / 1000.0
        last = screen_region(self.region())
        stable_hits = 0
        last_score = None
        while time.perf_counter() < end and not self.cancel_ev.is_set():
            time.sleep(0.04)
            cur = screen_region(self.region())
            score = image_change_score(last, cur)
            last_score = score
            if score <= threshold:
                stable_hits += 1
                if stable_hits >= 2:
                    self.append_log(f"朋友圈区域已稳定，帧差 {score:.2f}")
                    return True
            else:
                stable_hits = 0
            last = cur
        if last_score is not None:
            self.append_log(f"稳定等待结束，最后帧差 {last_score:.2f}")
        return False

    def test_more_button(self):
        try:
            pos = self.locate_more_button()
            if not pos:
                self.append_log("未找到朋友圈“…”按钮。请确认检测区包含按钮，或使用“F8取…按钮”手动兜底。")
                return
            self.last_more_predicted_pos = pos
            self.append_log(f"找到朋友圈“…”按钮: {pos[0]},{pos[1]}")
            set_cursor_pos(pos[0], pos[1])
        except Exception as exc:
            self.append_log(f"寻找“…”按钮失败: {exc}")

    def locate_more_button(self, quiet=False):
        if self.last_more_pos:
            rx, _, rw, _ = self.region()
            if not (rx <= self.last_more_pos[0] <= rx + rw):
                if not quiet:
                    self.append_log("手动记录的“…”X轴不在检测区内，请把检测区右边界框到“…”按钮右侧。")
                return None
            pos = locate_moments_more_button_fixed_x(self.region(), self.last_more_pos[0])
            if pos:
                return (pos[0], pos[1] + int(self.more_dy_v.get()))
            if not quiet:
                self.append_log("固定X窄条未找到“…”双点，退回模板搜索。")

        if self.more_template is not None:
            if self.last_more_pos:
                pos, score = locate_template_center_fixed_x(
                    self.more_template,
                    self.region(),
                    fixed_center_x=self.last_more_pos[0],
                )
                if pos:
                    return (pos[0], pos[1] + int(self.more_dy_v.get()))
                if not quiet:
                    self.append_log(f"固定X模板未匹配到“…”按钮，最佳差异 {score:.2f}，退回右侧模板搜索。")

            pos, score = locate_template_center(self.more_template, self.region())
            if pos:
                return pos
            if not quiet:
                self.append_log(f"模板未匹配到“…”按钮，最佳差异 {score:.2f}，退回颜色识别。")

        pos = locate_moments_more_button(self.region())
        if pos:
            return self.apply_more_offset(pos)
        return None

    def wait_more_button_stable(self, timeout_ms, frames, tolerance_px):
        end = time.perf_counter() + max(0, timeout_ms) / 1000.0
        need = max(1, int(frames))
        tol = max(0, int(tolerance_px))
        stable = []
        last_seen = None

        while time.perf_counter() < end and not self.cancel_ev.is_set():
            pos = self.locate_more_button(quiet=True)
            if pos:
                last_seen = pos
                if not stable:
                    stable = [pos]
                else:
                    base_x, base_y = stable[-1]
                    if abs(pos[0] - base_x) <= tol and abs(pos[1] - base_y) <= tol:
                        stable.append(pos)
                    else:
                        stable = [pos]

                if len(stable) >= need:
                    xs = [p[0] for p in stable[-need:]]
                    ys = [p[1] for p in stable[-need:]]
                    final = (round(sum(xs) / len(xs)), round(sum(ys) / len(ys)))
                    self.append_log(f"“…”坐标已稳定 {need} 帧: {final[0]},{final[1]}")
                    return final
            else:
                stable = []
            time.sleep(0.04)

        if last_seen:
            self.append_log(f"“…”坐标未达到稳定帧数，最后识别: {last_seen[0]},{last_seen[1]}")
        else:
            self.append_log("目标稳定等待结束，仍未识别到“…”按钮。")
        return None

    def apply_more_offset(self, pos):
        return (
            pos[0] + int(self.more_dx_v.get()),
            pos[1] + int(self.more_dy_v.get()),
        )

    def stop_requested(self):
        if self.cancel_ev.is_set():
            self.capture_mode = None
            self.ui_q.put(("status", "已终止"))
            return True
        return False

    def do_refresh(self):
        if self.stop_requested():
            return False
        left_click(int(self.refresh_x_v.get()), int(self.refresh_y_v.get()))
        return True

    def do_comment_action(self, more_pos=None):
        if self.stop_requested():
            return False
        if more_pos is None:
            more_pos = self.locate_more_button()
        if self.stop_requested():
            return False
        if more_pos is None and self.last_more_pos:
            more_pos = self.last_more_pos
            self.append_log("自动识别“…”失败，使用手动记录的“…”位置。")
        if not more_pos:
            raise RuntimeError("未找到朋友圈“…”按钮")
        self.last_more_pos = more_pos
        more_x, more_y = more_pos
        if self.stop_requested():
            return False
        left_click(more_x, more_y)
        time.sleep(0.08)
        if self.stop_requested():
            return False
        left_click(more_x + int(self.menu_dx_v.get()), more_y + int(self.menu_dy_v.get()))
        time.sleep(float(self.after_detect_delay_ms_v.get()) / 1000.0)
        if self.stop_requested():
            return False
        paste_text(self.comment_v.get(), self.root)
        if self.stop_requested():
            return False

        if self.has_send_offset():
            pred_x = more_x + int(self.send_dx_v.get())
            pred_y = more_y + int(self.send_dy_v.get())
            send_pos = wait_green_send_button_near(pred_x, pred_y, timeout=1.2, interval=0.03, cancel_ev=self.cancel_ev)
            if self.stop_requested():
                return False
            if send_pos:
                left_click(send_pos[0], send_pos[1])
                self.append_log(f"已在手动发送预测点附近点击绿色发送按钮: {send_pos[0]},{send_pos[1]} | 预测 {pred_x},{pred_y}")
            else:
                left_click(pred_x, pred_y)
                self.append_log(f"未在预测点附近识别到绿色按钮，已点击手动发送预测点: {pred_x},{pred_y}")
            return True

        send_pos = wait_green_send_button(min_y=more_y, timeout=0.8, interval=0.02, cancel_ev=self.cancel_ev)
        if self.stop_requested():
            return False
        if send_pos:
            left_click(send_pos[0], send_pos[1])
            self.append_log(f"已点击绿色发送按钮: {send_pos[0]},{send_pos[1]}")
            return True
        else:
            raise RuntimeError("未找到绿色发送按钮，已停止；朋友圈评论不能用 Enter 发送。")

    def has_send_offset(self):
        if self.send_offset_set:
            return True
        try:
            return int(self.send_dx_v.get()) != 0 or int(self.send_dy_v.get()) != 0
        except ValueError:
            return False

    def test_comment_action(self):
        self.append_log("测试留言动作：会点击“…”、评论、粘贴并发送，请确认可安全测试。")
        threading.Thread(target=self.do_comment_action, daemon=True).start()

    def cancel(self, source="button"):
        already = self.cancel_ev.is_set()
        self.cancel_ev.set()
        self.capture_mode = None
        if not already:
            if source == "F7":
                self.append_log("F7 已触发停止，当前任务会立即中断。")
            else:
                self.append_log("已触发停止，当前任务会立即中断。")
        self.ui_q.put(("status", "已终止"))

    def arm(self):
        with self.worker_lock:
            if self.worker_running:
                self.append_log("已有任务运行，已拦截重复启动。")
                return
            self.worker_running = True
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        self.cancel_ev.clear()
        set_timer_res(1)
        try:
            if self.baseline is None:
                self.baseline = screen_region(self.region())
                self.append_log("自动截取启动前检测区作为基准。")

            target_ts = datetime.strptime(self.target_v.get().strip(), "%Y-%m-%d %H:%M:%S.%f").timestamp()
            start_ts = target_ts - float(self.start_before_ms_v.get()) / 1000.0
            refresh_gap = max(0.03, float(self.refresh_ms_v.get()) / 1000.0)
            threshold = float(self.threshold_v.get())

            if start_ts <= time.time() - 2:
                self.append_log("目标时间已过或太近，请重新设置。")
                return

            bring_wechat_front()
            self.append_log("任务已启动，等待开刷时间。")
            self.ui_q.put(("status", "等待开刷"))

            kernel32.SetPriorityClass(kernel32.GetCurrentProcess(), HIGH_PRIORITY_CLASS)
            kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_HIGHEST)

            while not self.cancel_ev.is_set() and time.time() < start_ts:
                left = start_ts - time.time()
                if left > 1:
                    time.sleep(0.1)
                elif left > 0.05:
                    time.sleep(0.005)
                else:
                    time.sleep(0)

            if self.cancel_ev.is_set():
                return

            self.append_log("开始刷新并检测朋友圈变化。")
            self.ui_q.put(("status", "刷新检测中"))
            last_refresh = 0.0
            scores = []

            while not self.cancel_ev.is_set():
                now = time.perf_counter()
                if now - last_refresh >= refresh_gap:
                    if not self.do_refresh():
                        return
                    last_refresh = now
                    self.append_log("已刷新。")

                img = screen_region(self.region())
                score = image_change_score(self.baseline, img)
                scores.append(score)
                if len(scores) > 12:
                    scores.pop(0)

                if score >= threshold:
                    self.append_log(f"检测到朋友圈区域变化，分数 {score:.2f}，准备留言。")
                    self.ui_q.put(("status", "检测到变化"))
                    stable_wait_ms = float(self.stable_wait_ms_v.get())
                    stable_threshold = float(self.stable_threshold_v.get())
                    if stable_wait_ms > 0:
                        self.append_log(f"等待朋友圈区域稳定，最多 {stable_wait_ms:.0f}ms。")
                        self.wait_region_stable(stable_wait_ms, stable_threshold)
                    more_wait_ms = float(self.more_stable_wait_ms_v.get())
                    more_frames = int(self.more_stable_frames_v.get())
                    more_tol = int(self.more_stable_px_v.get())
                    self.append_log(f"等待“…”坐标稳定，最多 {more_wait_ms:.0f}ms，连续 {more_frames} 帧。")
                    more_pos = self.wait_more_button_stable(more_wait_ms, more_frames, more_tol)
                    if not more_pos:
                        self.append_log("检测到变化，但“…”坐标未稳定，继续刷新检测。")
                        time.sleep(0.03)
                        continue
                    self.append_log(f"已定位新朋友圈“…”按钮: {more_pos[0]},{more_pos[1]}")
                    if not self.do_comment_action(more_pos):
                        return
                    self.append_log("留言动作已触发。")
                    self.ui_q.put(("status", "已触发"))
                    return

                if len(scores) == 12 and statistics.mean(scores) > threshold * 0.8:
                    self.append_log(f"检测区持续接近阈值，均值 {statistics.mean(scores):.2f}")

                time.sleep(0.015)

        except Exception as exc:
            self.append_log(f"错误: {exc}")
            self.append_log(traceback.format_exc().splitlines()[-1])
            self.ui_q.put(("status", "错误"))
        finally:
            reset_timer_res(1)
            with self.worker_lock:
                self.worker_running = False


def show_fatal_error(exc):
    detail = traceback.format_exc()
    log_path = "moments_comment_sniper_error.log"
    try:
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(detail)
    except Exception:
        pass
    try:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"程序启动失败：\n{exc}\n\n详细错误已写入：{log_path}",
            "朋友圈留言抢车半自动版",
            0x10,
        )
    except Exception:
        print(detail)
        input("按回车退出...")


# ===================== 赛博朋克主题 (Cyberpunk 2077 / Edgerunners) =====================
CP_BG     = "#0a0e16"
CP_PANEL  = "#0f1622"
CP_INPUT  = "#070b12"
CP_BORDER = "#1c3a4a"
CP_FG     = "#d6f2ff"
CP_DIM    = "#5a7a8c"
CP_YELLOW = "#fcee0a"
CP_CYAN   = "#00f0ff"
CP_MAG    = "#ff2a6d"
CP_RED    = "#ff003c"
CP_TERM   = "#7df9e8"


def apply_cyberpunk_theme(root):
    try:
        root.configure(bg=CP_BG)
    except Exception:
        pass
    try:
        root.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
        val = ctypes.c_int(1)
        for attr in (20, 19):
            try:
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, attr, ctypes.byref(val), ctypes.sizeof(val))
            except Exception:
                pass
    except Exception:
        pass
    style = ttk.Style()
    try:
        style.theme_use("clam")
    except Exception:
        pass
    label_font = ("Microsoft YaHei UI", 9)
    style.configure(".", background=CP_BG, foreground=CP_FG,
                    fieldbackground=CP_INPUT, bordercolor=CP_BORDER,
                    lightcolor=CP_BORDER, darkcolor=CP_BORDER,
                    troughcolor=CP_PANEL, font=label_font,
                    focuscolor=CP_CYAN, insertcolor=CP_CYAN,
                    selectbackground=CP_MAG, selectforeground="#ffffff")
    style.configure("TFrame", background=CP_BG)
    style.configure("TLabel", background=CP_BG, foreground=CP_FG)
    style.configure("Hint.TLabel", background=CP_BG, foreground=CP_DIM)
    style.configure("Banner.TLabel", background=CP_BG, foreground=CP_YELLOW,
                    font=("Consolas", 17, "bold"))
    style.configure("Sub.TLabel", background=CP_BG, foreground=CP_CYAN,
                    font=("Consolas", 9))
    style.configure("TLabelframe", background=CP_BG, bordercolor=CP_CYAN,
                    relief="solid", borderwidth=1)
    style.configure("TLabelframe.Label", background=CP_BG, foreground=CP_YELLOW,
                    font=("Consolas", 10, "bold"))
    style.configure("TButton", background=CP_PANEL, foreground=CP_CYAN,
                    bordercolor=CP_BORDER, relief="flat", padding=(10, 5),
                    font=("Microsoft YaHei UI", 9, "bold"))
    style.map("TButton",
              background=[("pressed", "#0a121c"), ("active", "#14202e")],
              foreground=[("active", CP_YELLOW)],
              bordercolor=[("active", CP_CYAN)])
    style.configure("TEntry", fieldbackground=CP_INPUT, foreground=CP_FG,
                    bordercolor=CP_BORDER, insertcolor=CP_CYAN, padding=5)
    style.map("TEntry", bordercolor=[("focus", CP_CYAN)],
              lightcolor=[("focus", CP_CYAN)], darkcolor=[("focus", CP_CYAN)])
    style.configure("TCombobox", fieldbackground=CP_INPUT, background=CP_PANEL,
                    foreground=CP_FG, arrowcolor=CP_YELLOW, bordercolor=CP_BORDER)
    style.configure("Treeview", background=CP_PANEL, fieldbackground=CP_PANEL,
                    foreground=CP_FG, bordercolor=CP_BORDER, rowheight=26)
    style.map("Treeview", background=[("selected", CP_MAG)],
              foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=CP_BG, foreground=CP_YELLOW,
                    relief="flat", font=("Consolas", 9, "bold"))
    style.map("Treeview.Heading", background=[("active", CP_PANEL)],
              foreground=[("active", CP_CYAN)])
    style.configure("TScrollbar", background=CP_PANEL, troughcolor=CP_BG,
                    arrowcolor=CP_CYAN, bordercolor=CP_BORDER)
    return style


def main():
    enable_dpi_awareness()
    root = tk.Tk()
    apply_cyberpunk_theme(root)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        show_fatal_error(exc)
