import asyncio
import websockets
import tkinter as tk
import json
import queue
import threading
import re
import pystray
from PIL import Image, ImageDraw
import tkinter.font as tkfont
import time
import math
import ctypes
import numpy as np

# WASAPI 回环（系统内音频）
try:
    import pyaudiowpatch as pyaudio
except Exception:
    pyaudio = None
    print("开发者调试提示：未找到 pyaudiowpatch，将无法启用系统内音频律动条。安装: pip install pyaudiowpatch")

# 全局样式
BG_COLOR = "black"
HOVER_BG_COLOR = "#606060"
SONG_FG = "#FFD700"
LYRIC_FG = "#E6E6FA"
TRANSLATION_FG = "#98FB98"
FONT_NAME = "Microsoft YaHei UI"
LYRIC_FONT_SIZE = 28
TRANSLATION_FONT_SIZE = 18
SONG_FONT_SIZE = 14

WINDOW_HEIGHT = 200
WINDOW_ALPHA = 0.85
HOVER_ALPHA = 0.75
WEBSOCKET_PORT = 8765

# 卡拉OK参数
MAX_FPS_MOVING = 120
IDLE_FPS = 10
PAUSED_FPS = 2
KARAOKE_FADE_TIME = 0.30
KARAOKE_HL_COLOR = "#FFD700"
KARAOKE_SHIMMER = 0.0
LAST_LINE_FALLBACK = 3.0
OUTLINE_SIZE = 1
OUTLINE_COLOR = "#000000"
OUTLINE_NEIGHBORS = 4
TIME_FREEZE_ON_STALE_SEC = 0.8
RENDER_TRANSLATION_ON_CANVAS = True
TRANSLATION_TOP_GAP = 8
TRANSLATION_MATCH_WINDOW = 0.6

TRANSPARENT_KEY = "#FF00FF"

# ------- Windows 任务栏定位（Macos的先不写awa） -------
class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]

def _get_taskbar_rect():
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW("Shell_TrayWnd", None)
    rect = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect

def _get_screen_size():
    user32 = ctypes.windll.user32
    return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)

def _detect_taskbar_edge():
    screen_w, screen_h = _get_screen_size()
    r = _get_taskbar_rect()
    tb_w, tb_h = r.right - r.left, r.bottom - r.top
    if tb_w >= screen_w * 0.9 and tb_h < screen_h * 0.5:
        return ("top" if r.top <= 0 else "bottom", r)
    elif tb_h >= screen_h * 0.9 and tb_w < screen_w * 0.5:
        return ("left" if r.left <= 0 else "right", r)
    else:
        return ("bottom", r)

# ------- 音频线程（WASAPI 回环） -------
class _AudioWorker:
    def __init__(self, num_bars, on_levels, stop_event: threading.Event):
        self.num_bars = num_bars
        self.on_levels = on_levels
        self.stop_event = stop_event
        # 灵敏度与手感
        self.min_db = -20.0
        self.max_db = 70.0
        self.smooth_alpha = 0.65
        self.peak_decay = 1.0
        self.p = None
        self.stream = None
        self.rate = 48000
        self.chunk = 2048
        self.band_idx = None
        self.freqs = None
        self.display_levels = np.zeros(self.num_bars, dtype=np.float32)

    def _open_loopback_stream(self):
        self.p = pyaudio.PyAudio()
        try:
            wasapi_info = self.p.get_host_api_info_by_type(pyaudio.paWASAPI)
        except Exception:
            raise RuntimeError("未检测到 WASAPI，无法捕获系统内音频。")

        default_out = self.p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
        rate = int(default_out.get("defaultSampleRate", 48000)) or 48000
        # 优先尝试对默认输出使用 as_loopback=True（更通用）
        try:
            stream = self.p.open(format=pyaudio.paInt16,
                                 channels=min(2, int(default_out.get("maxOutputChannels", 2)) or 2),
                                 rate=rate,
                                 input=True,
                                 frames_per_buffer=self.chunk,
                                 input_device_index=default_out["index"],
                                 as_loopback=True)
            self.rate = rate
            return stream
        except Exception:
            pass

        # 退回：枚举 loopback 设备
        loopback = None
        for lb in self.p.get_loopback_device_info_generator():
            loopback = lb
            if default_out["name"] in lb["name"]:
                break
        if loopback is None:
            raise RuntimeError("未找到回放(Loopback)设备。")

        rate = int(loopback["defaultSampleRate"])
        channels = min(2, int(loopback.get("maxInputChannels", 2)) or 2)
        stream = self.p.open(format=pyaudio.paInt16,
                             channels=channels,
                             rate=rate,
                             input=True,
                             frames_per_buffer=self.chunk,
                             input_device_index=loopback["index"])
        self.rate = rate
        return stream

    def _prepare_fft_bands(self):
        self.freqs = np.fft.rfftfreq(self.chunk, d=1.0 / self.rate)
        f_min, f_max = 20.0, min(20000.0, self.rate / 2.0)
        edges = np.geomspace(f_min, f_max, self.num_bars + 1)
        self.band_idx = []
        for i in range(self.num_bars):
            lo, hi = edges[i], edges[i + 1]
            sel = np.where((self.freqs >= lo) & (self.freqs < hi))[0]
            if sel.size == 0:
                center = math.sqrt(lo * hi)
                nearest = int(np.argmin(np.abs(self.freqs - center)))
                sel = np.array([nearest], dtype=int)
            self.band_idx.append(sel)

    def run(self):
        try:
            self.stream = self._open_loopback_stream()
        except Exception as e:
            print(f"[律动条] 音频初始化失败：{e}")
            return
        self._prepare_fft_bands()
        window = np.hanning(self.chunk).astype(np.float32)
        while not self.stop_event.is_set():
            try:
                buf = self.stream.read(self.chunk, exception_on_overflow=False)
            except Exception:
                continue
            data = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
            if getattr(self.stream, "_channels", 1) >= 2:
                try:
                    data = data.reshape(-1, self.stream._channels).mean(axis=1)
                except Exception:
                    pass
            x = data[:self.chunk] * window
            spec = np.fft.rfft(x)
            mag = np.abs(spec) + 1e-10
            db = 20.0 * np.log10(mag)
            band_vals = np.empty(self.num_bars, dtype=np.float32)
            for i, sel in enumerate(self.band_idx):
                band_vals[i] = db[sel].max()
            levels = (band_vals - self.min_db) / (self.max_db - self.min_db)
            levels = np.clip(levels, 0.0, 1.0)
            prev = self.display_levels
            up = np.maximum(levels, prev * (1.0 - self.peak_decay))
            smoothed = self.smooth_alpha * prev + (1.0 - self.smooth_alpha) * up
            self.display_levels = smoothed
            self.on_levels(self.display_levels.copy())
        try:
            if self.stream and self.stream.is_active():
                self.stream.stop_stream()
        except Exception:
            pass
        try:
            if self.stream:
                self.stream.close()
        except Exception:
            pass
        try:
            if self.p:
                self.p.terminate()
        except Exception:
            pass

# ------- 透明、贴任务栏的律动条覆盖层 -------
class VisualizerOverlay:
    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)

        # 用统一色键作为背景，后续用 Win32 抠掉这类像素
        self.win.configure(bg=TRANSPARENT_KEY)
        try:
            self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass

        # 任务栏贴边定位
        edge, tb_rect = _detect_taskbar_edge()
        screen_w, screen_h = _get_screen_size()
        self.strip_height_px = 72
        self.side_strip_px = 120
        self.bar_spacing_px = 2
        self.min_bar_px = 2
        self.bar_color = "#00ff7f"

        if edge in ("bottom", "top"):
            win_w = screen_w
            win_h = self.strip_height_px
            x = 0
            y = max(0, tb_rect.top - win_h) if edge == "bottom" else max(0, tb_rect.bottom)
        else:
            win_w = self.side_strip_px
            win_h = screen_h
            x = max(0, tb_rect.right) if edge == "left" else max(0, tb_rect.left - win_w)
            y = 0

        self.vertical_layout = (win_h > win_w)
        self.win.geometry(f"{win_w}x{win_h}+{x}+{y}")

        # 画布背景也用透明键色，避免出现可见背景
        self.canvas = tk.Canvas(self.win, width=win_w, height=win_h,
                                bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)

        self.win.update_idletasks()
        self._apply_click_through_and_colorkey()  # 鼠标穿透 + 颜色键

        # 柱条布局
        if not self.vertical_layout:
            full = self.min_bar_px + self.bar_spacing_px
            self.num_bars = min(max(64, win_w // max(1, full)), 240)
            total_spacing = (self.num_bars + 1) * self.bar_spacing_px
            avail = max(1, win_w - total_spacing)
            self.bar_w = max(self.min_bar_px, avail // self.num_bars)
            self.bar_h = win_h
        else:
            full = self.min_bar_px + self.bar_spacing_px
            self.num_bars = min(max(64, win_h // max(1, full)), 240)
            total_spacing = (self.num_bars + 1) * self.bar_spacing_px
            avail = max(1, win_h - total_spacing)
            self.bar_w = max(self.min_bar_px, (win_w - 2 * self.bar_spacing_px))
            self.bar_h = max(self.min_bar_px, avail // self.num_bars)

        # 预创建矩形（初始高度/宽度为 0）
        self.bars = []
        if not self.vertical_layout:
            for i in range(self.num_bars):
                x1 = self.bar_spacing_px + i * (self.bar_w + self.bar_spacing_px)
                x2 = x1 + self.bar_w
                y2 = win_h
                y1 = y2
                r = self.canvas.create_rectangle(x1, y1, x2, y2, fill=self.bar_color, width=0)
                self.bars.append(r)
        else:
            for i in range(self.num_bars):
                y1 = self.bar_spacing_px + i * (self.bar_h + self.bar_spacing_px)
                y2 = y1 + self.bar_h
                x1 = 0
                x2 = 0
                r = self.canvas.create_rectangle(x1, y1, x2, y2, fill=self.bar_color, width=0)
                self.bars.append(r)

        # 音频线程
        self._stop_evt = threading.Event()
        self.worker = None
        self.thread = None
        self._running = False
        if pyaudio is not None:
            self._start_audio()

        self.alive = True
        self._visible = True

    def _apply_click_through_and_colorkey(self):
        # 顶层和 Canvas 都设置分层+透明命中，并设置颜色键（与 Tk 的 -transparentcolor 一致）
        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        LWA_COLORKEY = 0x00000001

        user32 = ctypes.windll.user32
        GetWindowLong = user32.GetWindowLongW
        SetWindowLong = user32.SetWindowLongW
        SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
        SetWindowPos = user32.SetWindowPos
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_NOZORDER = 0x0004
        SWP_FRAMECHANGED = 0x0020

        col = TRANSPARENT_KEY.lstrip("#")
        r, g, b = int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)
        colorref = r | (g << 8) | (b << 16)

        def _apply(hwnd):
            ex = GetWindowLong(hwnd, GWL_EXSTYLE)
            ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
            SetWindowLong(hwnd, GWL_EXSTYLE, ex)
            SetLayeredWindowAttributes(hwnd, colorref, 255, LWA_COLORKEY)
            SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)

        try:
            _apply(self.win.winfo_id())
        except Exception:
            pass
        try:
            _apply(self.canvas.winfo_id())
        except Exception:
            pass

        self._apply_click_through_to_all_children()

    def _apply_click_through_to_all_children(self):
        # 防御性地把所有子窗口都设置为穿透+颜色键，避免某些系统拦截事件
        EnumChildWindows = ctypes.windll.user32.EnumChildWindows
        GetWindowLong = ctypes.windll.user32.GetWindowLongW
        SetWindowLong = ctypes.windll.user32.SetWindowLongW
        SetLayeredWindowAttributes = ctypes.windll.user32.SetLayeredWindowAttributes

        GWL_EXSTYLE = -20
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_LAYERED = 0x00080000
        WS_EX_TOOLWINDOW = 0x00000080
        LWA_COLORKEY = 0x00000001

        col = TRANSPARENT_KEY.lstrip("#")
        r, g, b = int(col[0:2], 16), int(col[2:4], 16), int(col[4:6], 16)
        colorref = r | (g << 8) | (b << 16)

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _enum_proc(hwnd, lparam):
            try:
                ex = GetWindowLong(hwnd, GWL_EXSTYLE)
                ex |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_TOOLWINDOW
                SetWindowLong(hwnd, GWL_EXSTYLE, ex)
                SetLayeredWindowAttributes(hwnd, colorref, 255, LWA_COLORKEY)
            except Exception:
                pass
            return True

        try:
            EnumChildWindows(self.win.winfo_id(), _enum_proc, 0)
        except Exception:
            pass

    def _start_audio(self):
        if self._running or pyaudio is None:
            return
        self._stop_evt.clear()

        def on_levels(arr):
            try:
                self.win.after(0, self._update_bars, arr)
            except Exception:
                pass

        self.worker = _AudioWorker(self.num_bars, on_levels, self._stop_evt)
        self.thread = threading.Thread(target=self.worker.run, daemon=True)
        self.thread.start()
        self._running = True

    def _stop_audio(self):
        if not self._running:
            return
        try:
            self._stop_evt.set()
        except Exception:
            pass
        self._running = False

    def _update_bars(self, levels):
        if not self.alive or not self._visible:
            return
        if not self.vertical_layout:
            h = int(self.canvas.winfo_height())
            for i, lv in enumerate(levels):
                bh = int(lv * h)
                x1 = self.bar_spacing_px + i * (self.bar_w + self.bar_spacing_px)
                x2 = x1 + self.bar_w
                y2 = h
                y1 = max(0, y2 - bh)
                self.canvas.coords(self.bars[i], x1, y1, x2, y2)
        else:
            w = int(self.canvas.winfo_width())
            for i, lv in enumerate(levels):
                bw = int(lv * w)
                y1 = self.bar_spacing_px + i * (self.bar_h + self.bar_spacing_px)
                y2 = y1 + self.bar_h
                x2 = w
                x1 = max(0, x2 - bw)
                self.canvas.coords(self.bars[i], x1, y1, x2, y2)

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self._apply_click_through_and_colorkey()  # 有些系统隐藏/显示后需要重置样式
        self._start_audio()
        self._visible = True

    def hide(self):
        self._stop_audio()
        self.win.withdraw()
        self._visible = False

    def destroy(self):
        if not self.alive:
            return
        self.alive = False
        self._stop_audio()
        try:
            self.win.destroy()
        except Exception:
            pass

# ------- 歌词主窗口 -------
class DesktopLyrics:
    TIME_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Harmonia桌面歌词")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", BG_COLOR)
        screen_width = self.root.winfo_screenwidth()
        self.root.geometry(f"{screen_width}x{WINDOW_HEIGHT}+0+100")
        self.root.config(bg=BG_COLOR)

        self._build_fonts()

        # 状态
        self.connection_status = "disconnected"
        self.current_lyric = ""
        self.current_translation = ""
        self.current_song = ""
        self.current_artist = ""
        self.lyrics_data = []
        self.translations_data = []
        self.last_lyric_index = -1
        self.last_translation_index = -1
        self.has_lyrics = False
        self.karaoke_enabled = True

        # 律动条
        self.visualizer_enabled = True
        self.visualizer = None

        # 锁定
        self.is_locked = False

        self._build_ui()

        # 队列/托盘
        self.message_queue = queue.Queue()
        self.root.after(100, self.process_queue)
        self.tray_icon = None
        self._create_tray_icon()

        self.connected_clients = set()

        # 拖动与时间推进
        self.drag_data = {"x": 0, "y": 0, "dragging": False}
        self._last_sync_time = 0.0
        self._last_sync_mono = time.perf_counter()

        # 布局缓存
        self.current_line_start = 0.0
        self.next_line_start = 0.0
        self._need_layout = True
        self._line_positions = []
        self._line_width = 0
        self._char_items = []
        self._outline_items = []
        self._trans_item = None
        self._trans_outline_items = []

        # 动画循环
        self.root.after(self._frame_delay_ms(IDLE_FPS), self.animation_tick)

        # 启动律动条（默认开）
        if self.visualizer_enabled and pyaudio is not None:
            try:
                self.visualizer = VisualizerOverlay(self.root)
                self.visualizer.show()
            except Exception as e:
                print(f"创建律动条失败：{e}")
                self.visualizer = None

    def _build_fonts(self):
        self.lyric_font = tkfont.Font(family=FONT_NAME, size=LYRIC_FONT_SIZE, weight="bold")
        self.translation_font = tkfont.Font(family=FONT_NAME, size=TRANSLATION_FONT_SIZE, weight="normal")
        self.song_font = tkfont.Font(family=FONT_NAME, size=SONG_FONT_SIZE, weight="bold")

    def _build_ui(self):
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.song_label = tk.Label(self.main_frame, text="等待连接...", font=self.song_font,
                                   fg=SONG_FG, bg=BG_COLOR, pady=8)
        self.song_label.pack(anchor="center")
        self.lyric_canvas = tk.Canvas(self.main_frame, bg=BG_COLOR, highlightthickness=0)
        self.lyric_canvas.pack(expand=True, fill="both")
        self.lyric_canvas.bind("<Configure>", lambda e: self.invalidate_layout())
        self.translation_label = tk.Label(self.main_frame, text="", font=self.translation_font,
                                          fg=TRANSLATION_FG, bg=BG_COLOR, pady=8)

        self.root.bind("<ButtonPress-1>", self._start_move)
        self.root.bind("<ButtonRelease-1>", self._stop_move)
        self.root.bind("<B1-Motion>", self._on_move)
        self.root.bind("<Enter>", self._on_enter)
        self.root.bind("<Leave>", self._on_leave)
        self.root.attributes("-alpha", WINDOW_ALPHA)

    # 交互
    def _on_enter(self, _):
        if not self.is_locked:
            self._change_bg(HOVER_BG_COLOR)
            self.root.attributes("-alpha", HOVER_ALPHA)

    def _on_leave(self, _):
        if not self.is_locked:
            self._change_bg(BG_COLOR)
            self.root.attributes("-alpha", WINDOW_ALPHA)

    def _change_bg(self, color):
        self.main_frame.config(bg=color)
        self.song_label.config(bg=color)
        self.translation_label.config(bg=color)
        self.lyric_canvas.config(bg=color)

    def _toggle_lock(self, *_):
        def _do():
            self.is_locked = not self.is_locked
            if self.is_locked:
                self._change_bg(BG_COLOR)
                self.root.attributes("-alpha", WINDOW_ALPHA)
            self._update_tray_menu()
        self.root.after(0, _do)

    def _toggle_karaoke(self, *_):
        def _do():
            self.karaoke_enabled = not self.karaoke_enabled
            self._need_layout = True
            self._prepare_line_layout()
            self._rebuild_items()
            self._update_tray_menu()
        self.root.after(0, _do)

    def _toggle_visualizer(self, *_):
        def _do():
            self.visualizer_enabled = not self.visualizer_enabled
            if self.visualizer_enabled:
                if pyaudio is None:
                    print("律动条无法启用：缺少 pyaudiowpatch。请安装：pip install pyaudiowpatch")
                    self.visualizer_enabled = False
                else:
                    if self.visualizer is None:
                        try:
                            self.visualizer = VisualizerOverlay(self.root)
                        except Exception as e:
                            print(f"创建律动条失败：{e}")
                            self.visualizer = None
                            self.visualizer_enabled = False
                    if self.visualizer:
                        self.visualizer.show()
            else:
                if self.visualizer:
                    self.visualizer.hide()
            self._update_tray_menu()
        self.root.after(0, _do)

    def _start_move(self, event):
        if not self.is_locked:
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            self.drag_data["dragging"] = True

    def _stop_move(self, _):
        self.drag_data["dragging"] = False

    def _on_move(self, event):
        if not self.drag_data["dragging"] or self.is_locked:
            return
        deltax = event.x - self.drag_data["x"]
        deltay = event.y - self.drag_data["y"]
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = self.root.winfo_width()
        x = max(0, min(x, screen_width - window_width))
        y = max(0, min(y, screen_height - WINDOW_HEIGHT))
        self.root.geometry(f"+{x}+{y}")

    # 托盘
    def _update_tray_menu(self):
        if self.tray_icon:
            try:
                self.tray_icon.update_menu()
            except Exception:
                pass

    def _create_tray_icon(self):
        try:
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.ellipse([(20, 12), (44, 36)], fill="#E6E6FA", outline="#FFFFFF", width=2)
            draw.rectangle([(42, 18), (46, 50)], fill="#E6E6FA")
            points = [(38, 28), (52, 22), (52, 34), (38, 28)]
            draw.polygon(points, fill="#E6E6FA")
            self.tray_menu = pystray.Menu(
                pystray.MenuItem(lambda _: "解锁" if self.is_locked else "锁定", self._toggle_lock),
                pystray.MenuItem(lambda _: f"逐字渐变：{'开' if self.karaoke_enabled else '关'}",
                                 self._toggle_karaoke),
                pystray.MenuItem(lambda _: f"律动条：{'开' if self.visualizer_enabled else '关'}",
                                 self._toggle_visualizer),
                pystray.MenuItem("断开连接", self._disconnect_client),
                pystray.MenuItem("退出", self._quit)
            )
            self.tray_icon = pystray.Icon("harmonia_lyrics", image, "Harmonia桌面歌词", self.tray_menu)
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            print(f"创建托盘图标失败: {e}")

    def _disconnect_client(self, *_):
        if self.connected_clients:
            print("主动断开客户端连接")
            for client in list(self.connected_clients):
                try:
                    asyncio.run_coroutine_threadsafe(client.close(), asyncio.get_event_loop())
                except Exception as e:
                    print(f"断开连接时出错: {e}")
            self.connected_clients.clear()
            self.safe_update("clear")
            self.safe_update("status", "disconnected")

    def _quit(self, *_):
        print("退出应用程序")
        try:
            if self.visualizer:
                self.visualizer.destroy()
        except Exception:
            pass
        self._disconnect_client()
        self.root.after(100, self.root.destroy)
        if self.tray_icon:
            try:
                self.tray_icon.stop()
            except Exception:
                pass

    # 连接状态
    def update_status(self, status):
        self.connection_status = status
        if status == "connected":
            self.song_label.config(text="已连接 - 等待歌曲...", fg=SONG_FG)
        elif status == "disconnected":
            self.song_label.config(text="等待连接...", fg=SONG_FG)

    # 解析歌词
    def _normalize_lyric_payload(self, payload):
        if isinstance(payload, dict):
            return payload.get("lyric") or ""
        return payload or ""

    def parse_lyrics(self, payload):
        text = self._normalize_lyric_payload(payload)
        if not text:
            return []
        entries = []
        for raw in text.splitlines():
            line = raw.strip("\n")
            if not line:
                continue
            tags = list(self.TIME_TAG_RE.finditer(line))
            if not tags:
                continue
            pure = self.TIME_TAG_RE.sub("", line).strip()
            if pure == "":
                continue
            for m in tags:
                mm = int(m.group(1))
                ss = int(m.group(2))
                frac = m.group(3)
                if frac is None:
                    ms = 0
                else:
                    if len(frac) == 1:
                        ms = int(frac) * 100
                    elif len(frac) == 2:
                        ms = int(frac) * 10
                    else:
                        ms = int(frac[:3])
                t = mm * 60 + ss + ms / 1000.0
                entries.append({"time": t, "text": pure})
        entries.sort(key=lambda x: x["time"])
        return entries

    # 时间推进
    def _sync_time(self, server_time: float):
        try:
            self._last_sync_time = float(server_time)
        except Exception:
            self._last_sync_time = 0.0
        self._last_sync_mono = time.perf_counter()

    def _now_playback_time(self) -> float:
        dt = time.perf_counter() - self._last_sync_mono
        if dt > TIME_FREEZE_ON_STALE_SEC:
            return self._last_sync_time
        return self._last_sync_time + dt

    # 布局缓存
    def invalidate_layout(self):
        self._need_layout = True

    def _prepare_line_layout(self):
        s = self.current_lyric or ""
        if not s:
            self._line_positions = []
            self._line_width = 0
            self._need_layout = False
            return
        canvas_w = max(1, self.lyric_canvas.winfo_width())
        widths = [self.lyric_font.measure(ch) for ch in s]
        total_w = self.lyric_font.measure(s) if not self.karaoke_enabled else sum(widths)
        x = (canvas_w - total_w) // 2
        pos = []
        if self.karaoke_enabled:
            for ch, w in zip(s, widths):
                pos.append((ch, x))
                x += w
        self._line_positions = pos
        self._line_width = total_w
        self._need_layout = False

    def _build_outline_offsets(self):
        o = OUTLINE_SIZE
        if o <= 0:
            return []
        if OUTLINE_NEIGHBORS >= 8:
            return [(-o, 0), (o, 0), (0, -o), (0, o), (-o, -o), (-o, o), (o, -o), (o, o)]
        else:
            return [(-o, 0), (o, 0), (0, -o), (0, o)]

    def _clear_placeholder(self):
        try:
            self.lyric_canvas.delete("placeholder")
        except Exception:
            pass

    def _rebuild_items(self):
        self._clear_placeholder()
        for ids in self._outline_items:
            for iid in ids:
                self.lyric_canvas.delete(iid)
        for iid in self._char_items:
            self.lyric_canvas.delete(iid)
        if self._trans_item:
            self.lyric_canvas.delete(self._trans_item)
            self._trans_item = None
        for iid in self._trans_outline_items:
            self.lyric_canvas.delete(iid)
        self._trans_outline_items = []
        self._char_items = []
        self._outline_items = []

        s = self.current_lyric or ""
        canvas_w = max(1, self.lyric_canvas.winfo_width())
        canvas_h = max(1, self.lyric_canvas.winfo_height())
        line_space = self.lyric_font.metrics("linespace")
        y = (canvas_h - line_space) // 2
        outline_offsets = self._build_outline_offsets()

        if not s:
            return

        if self.karaoke_enabled:
            for (ch, x) in self._line_positions:
                one_outline_ids = []
                for dx, dy in outline_offsets:
                    oid = self.lyric_canvas.create_text(
                        x + dx, y + dy, text=ch, fill=OUTLINE_COLOR, font=self.lyric_font, anchor="nw"
                    )
                    one_outline_ids.append(oid)
                self._outline_items.append(one_outline_ids)
                mid = self.lyric_canvas.create_text(
                    x, y, text=ch, fill=LYRIC_FG, font=self.lyric_font, anchor="nw"
                )
                self._char_items.append(mid)
        else:
            total_w = self._line_width or self.lyric_font.measure(s)
            x0 = (canvas_w - total_w) // 2
            one_outline_ids = []
            for dx, dy in outline_offsets:
                oid = self.lyric_canvas.create_text(
                    x0 + dx, y + dy, text=s, fill=OUTLINE_COLOR, font=self.lyric_font, anchor="nw"
                )
                one_outline_ids.append(oid)
            self._outline_items.append(one_outline_ids)
            mid = self.lyric_canvas.create_text(
                x0, y, text=s, fill=LYRIC_FG, font=self.lyric_font, anchor="nw"
            )
            self._char_items.append(mid)

        if RENDER_TRANSLATION_ON_CANVAS and self.current_translation:
            trans = self.current_translation
            trans_width = self.translation_font.measure(trans)
            tx = (canvas_w - trans_width) // 2
            ty = y + line_space + TRANSLATION_TOP_GAP
            for dx, dy in outline_offsets:
                oid = self.lyric_canvas.create_text(
                    tx + dx, ty + dy, text=trans, fill=OUTLINE_COLOR,
                    font=self.translation_font, anchor="nw"
                )
                self._trans_outline_items.append(oid)
            self._trans_item = self.lyric_canvas.create_text(
                tx, ty, text=trans, fill=TRANSLATION_FG, font=self.translation_font, anchor="nw"
            )

    # 逻辑更新
    def update_lyrics_with_time(self, current_time):
        if not hasattr(self, "last_translation_index"):
            self.last_translation_index = -1
        if not self.lyrics_data:
            return

        current_index = -1
        for i, lyric in enumerate(self.lyrics_data):
            if lyric['time'] <= current_time:
                current_index = i
            else:
                break

        line_changed = False
        if current_index != -1 and current_index != self.last_lyric_index:
            self.last_lyric_index = current_index
            self.current_lyric = self.lyrics_data[current_index]['text']
            self.current_line_start = self.lyrics_data[current_index]['time']
            if current_index + 1 < len(self.lyrics_data):
                self.next_line_start = self.lyrics_data[current_index + 1]['time']
            else:
                self.next_line_start = self.current_line_start + LAST_LINE_FALLBACK
            self._need_layout = True
            line_changed = True

        # 翻译与主行时间接近才显示
        if self.translations_data and current_index != -1:
            target_t = self.lyrics_data[current_index]['time']
            best_idx = -1
            best_diff = 1e9
            for i, tline in enumerate(self.translations_data):
                diff = abs(tline['time'] - target_t)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
                if tline['time'] - target_t > TRANSLATION_MATCH_WINDOW:
                    break
            if best_idx != -1 and best_diff <= TRANSLATION_MATCH_WINDOW:
                if best_idx != self.last_translation_index or self.current_translation != self.translations_data[best_idx]['text']:
                    self.last_translation_index = best_idx
                    self.current_translation = self.translations_data[best_idx]['text']
                    self.translation_label.config(text=self.current_translation)
                    line_changed = True
            else:
                if self.last_translation_index != -1 or self.current_translation:
                    self.last_translation_index = -1
                    self.current_translation = ""
                    self.translation_label.config(text="")
                    line_changed = True
        else:
            if self.current_translation:
                self.current_translation = ""
                self.translation_label.config(text="")
                line_changed = True

        if line_changed or self._need_layout:
            self._prepare_line_layout()
            self._rebuild_items()

    # 渲染
    def _ease_in_out(self, t: float) -> float:
        t = max(0.0, min(1.0, t))
        return t * t * (3 - 2 * t)

    def _hex_to_rgb(self, hx: str):
        hx = hx.lstrip('#')
        return (int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16))

    def _rgb_to_hex(self, rgb):
        return "#{:02X}{:02X}{:02X}".format(*rgb)

    def _lerp_color_hex(self, a_hex, b_hex, t: float):
        a = self._hex_to_rgb(a_hex)
        b = self._hex_to_rgb(b_hex)
        t = max(0.0, min(1.0, t))
        return self._rgb_to_hex(tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3)))

    def _any_char_animating(self, now):
        if not self.karaoke_enabled:
            return False
        if not self._char_items or not self.current_lyric:
            return False
        s = self.current_lyric
        start_t = self.current_line_start
        end_t = max(start_t + 0.01, self.next_line_start)
        total = end_t - start_t
        n = max(1, len(s))
        char_delay = total / n
        fade_t = min(KARAOKE_FADE_TIME, max(0.05, char_delay * 0.99))
        idxs = {0, n - 1, n // 2, n // 4, (3 * n) // 4}
        for i in idxs:
            ch_start = start_t + i * char_delay
            p = (now - ch_start) / fade_t
            if 0.0 < p < 1.0:
                return True
        return False

    def _frame_delay_ms(self, fps):
        fps = max(1, int(round(fps)))
        return int(1000 / fps)

    def animation_tick(self):
        now = self._now_playback_time()
        self.update_lyrics_with_time(now)

        if self.karaoke_enabled and self._char_items and self.current_lyric:
            s = self.current_lyric
            start_t = self.current_line_start
            end_t = max(start_t + 0.01, self.next_line_start)
            total = end_t - start_t
            n = max(1, len(s))
            char_delay = total / n
            fade_t = min(KARAOKE_FADE_TIME, max(0.05, char_delay * 0.9))
            base = LYRIC_FG
            hl = KARAOKE_HL_COLOR
            do_shimmer = KARAOKE_SHIMMER > 0.0
            for i, mid in enumerate(self._char_items):
                ch_start = start_t + i * char_delay
                p = self._ease_in_out((now - ch_start) / fade_t)
                color = self._lerp_color_hex(base, hl, p)
                if do_shimmer and p > 0.5:
                    shimmer = KARAOKE_SHIMMER * max(0.0, math.sin(now * 6.28 + i * 0.6))
                    color = self._lerp_color_hex(color, "#FFFFFF", shimmer)
                self.lyric_canvas.itemconfig(mid, fill=color)

        dt = time.perf_counter() - self._last_sync_mono
        if dt > TIME_FREEZE_ON_STALE_SEC:
            next_delay = self._frame_delay_ms(PAUSED_FPS)
        else:
            moving = self._any_char_animating(now)
            next_delay = self._frame_delay_ms(MAX_FPS_MOVING if moving else IDLE_FPS)
        self.root.after(next_delay, self.animation_tick)

    # 队列/消息
    def safe_update(self, msg_type, data=None):
        self.message_queue.put((msg_type, data))

    def process_queue(self):
        processed = 0
        max_processed = 10
        try:
            while processed < max_processed and not self.message_queue.empty():
                msg_type, data = self.message_queue.get_nowait()
                if msg_type == "status":
                    self.update_status(data)
                elif msg_type == "song":
                    self.current_song = data.get('song', '')
                    self.current_artist = data.get('artist', '')
                    song_text = f"{self.current_song} - {self.current_artist}"[:80]
                    self.song_label.config(text=song_text, fg=SONG_FG)

                    self.has_lyrics = False
                    self.lyrics_data = []
                    self.translations_data = []
                    self.last_lyric_index = -1
                    self.last_translation_index = -1
                    self.current_lyric = ""
                    self.current_translation = ""
                    for ids in self._outline_items:
                        for iid in ids:
                            self.lyric_canvas.delete(iid)
                    for iid in self._char_items:
                        self.lyric_canvas.delete(iid)
                    if self._trans_item:
                        self.lyric_canvas.delete(self._trans_item)
                        self._trans_item = None
                    for iid in self._trans_outline_items:
                        self.lyric_canvas.delete(iid)
                    self._trans_outline_items = []
                    self._char_items = []
                    self._outline_items = []
                    self._line_positions = []
                    self._line_width = 0
                    self._need_layout = True
                    self._draw_center_text("正在加载歌词...", LYRIC_FG)
                    self.translation_label.config(text="")
                elif msg_type == "full_lyric":
                    self._update_full_lyrics(data.get('lyric', ''), data.get('tlyric', ''))
                elif msg_type == "time":
                    self._sync_time(data)
                    self.update_lyrics_with_time(self._now_playback_time())
                elif msg_type == "clear":
                    for ids in self._outline_items:
                        for iid in ids:
                            self.lyric_canvas.delete(iid)
                    for iid in self._char_items:
                        self.lyric_canvas.delete(iid)
                    if self._trans_item:
                        self.lyric_canvas.delete(self._trans_item)
                        self._trans_item = None
                    for iid in self._trans_outline_items:
                        self.lyric_canvas.delete(iid)
                    self._trans_outline_items = []
                    self._char_items = []
                    self._outline_items = []
                    self._line_positions = []
                    self._line_width = 0

                    self.translation_label.config(text="")
                    self.song_label.config(text="等待连接...", fg=SONG_FG)
                    self.current_lyric = ""
                    self.current_translation = ""
                    self.current_song = ""
                    self.current_artist = ""
                    self.lyrics_data = []
                    self.translations_data = []
                    self.last_lyric_index = -1
                    self.last_translation_index = -1
                    self.has_lyrics = False
                processed += 1
        except Exception as e:
            print(f"处理队列时出错: {e}")
        self.root.after(100, self.process_queue)

    def _update_full_lyrics(self, lyric, tlyric):
        try:
            self.lyrics_data = self.parse_lyrics(lyric) if lyric else []
            self.translations_data = self.parse_lyrics(tlyric) if tlyric else []
            self.last_lyric_index = -1
            self.last_translation_index = -1
            self.current_translation = ""
            self.translation_label.config(text="")
            self.has_lyrics = bool(self.lyrics_data)
            self._clear_placeholder()
            if not self.has_lyrics:
                self._draw_center_text("当前歌曲无歌词/正在等待网页传输", LYRIC_FG)
        except Exception as e:
            print(f"解析歌词时出错: {e}")
            self._draw_center_text("歌词解析错误", LYRIC_FG)

    def _draw_center_text(self, text: str, color: str):
        self.lyric_canvas.delete("all")
        if not text:
            return
        canvas_w = max(1, self.lyric_canvas.winfo_width())
        canvas_h = max(1, self.lyric_canvas.winfo_height())
        line_space = self.lyric_font.metrics("linespace")
        x = canvas_w // 2
        y = (canvas_h - line_space) // 2
        self.lyric_canvas.create_text(
            x, y, text=text, fill=color, font=self.lyric_font,
            anchor="n", justify="center", tags=("placeholder",)
        )

    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            self._quit()
        except Exception as e:
            print(f"主循环错误: {e}")
            self._quit()

# ------- WebSocket 服务器 -------
def start_websocket_server(desktop_lyrics):
    async def handle_connection(websocket):
        print("客户端已连接")
        desktop_lyrics.connected_clients.add(websocket)
        desktop_lyrics.safe_update("status", "connected")
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data.get('type') == 'ping':
                        await websocket.send(json.dumps({'type': 'pong'}))
                        continue
                    msg_type = data.get('type')
                    if msg_type == 'song':
                        desktop_lyrics.safe_update("song", {
                            'song': data.get('song', ''),
                            'artist': data.get('artist', '')
                        })
                    elif msg_type == 'full_lyric':
                        desktop_lyrics.safe_update("full_lyric", {
                            'lyric': data.get('lyric', ''),
                            'tlyric': data.get('tlyric', '')
                        })
                    elif msg_type == 'time':
                        desktop_lyrics.safe_update("time", data.get('currentTime', 0))
                except json.JSONDecodeError:
                    print("收到无效的JSON消息")
                except Exception as e:
                    print(f"处理消息时出错: {e}")
        except websockets.exceptions.ConnectionClosed:
            print("客户端已断开连接")
            desktop_lyrics.safe_update("status", "disconnected")
        except Exception as e:
            print(f"连接错误: {e}")
            desktop_lyrics.safe_update("status", "disconnected")
        finally:
            if websocket in desktop_lyrics.connected_clients:
                desktop_lyrics.connected_clients.remove(websocket)

    async def websocket_server():
        async with websockets.serve(handle_connection, "localhost", WEBSOCKET_PORT):
            print(f"WebSocket服务器已启动，监听端口 {WEBSOCKET_PORT}")
            await asyncio.Future()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(websocket_server())
    except Exception as e:
        print(f"WebSocket服务器错误: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    app = DesktopLyrics()
    server_thread = threading.Thread(target=start_websocket_server, args=(app,), daemon=True)
    server_thread.start()
    app.run()
    print("程序已退出")
