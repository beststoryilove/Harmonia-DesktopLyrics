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

# 颜色和样式
BG_COLOR = "black"
HOVER_BG_COLOR = "#606060"
SONG_FG = "#FFD700"
LYRIC_FG = "#E6E6FA"
TRANSLATION_FG = "#98FB98"
FONT_NAME = "Microsoft YaHei UI"
LYRIC_FONT_SIZE = 28
TRANSLATION_FONT_SIZE = 18
SONG_FONT_SIZE = 14

# 窗口
WINDOW_HEIGHT = 200
WINDOW_ALPHA = 0.85
HOVER_ALPHA = 0.75
WEBSOCKET_PORT = 8765

# 卡拉OK参数（降低默认占用）
MAX_FPS_MOVING = 30     # 动画进行时帧率
IDLE_FPS = 10           # 静止状态帧率（例如整行已高亮）
PAUSED_FPS = 2          # 网页暂停（长期未收到 time）时帧率
KARAOKE_FADE_TIME = 0.30
KARAOKE_HL_COLOR = "#FFD700"
KARAOKE_SHIMMER = 0.0   # 默认关闭闪烁；要效果可设为 0.05
LAST_LINE_FALLBACK = 3.0
OUTLINE_SIZE = 1
OUTLINE_COLOR = "#000000"
OUTLINE_NEIGHBORS = 4   # 4=上下左右，8=再加4个角

# 暂停检测：超过该时长未收到新的 time，就冻结本地时间推进
TIME_FREEZE_ON_STALE_SEC = 0.8

# 翻译渲染（在 Canvas 上静态绘制）
RENDER_TRANSLATION_ON_CANVAS = True
TRANSLATION_TOP_GAP = 8

# 翻译匹配窗口：仅当翻译时间与当前主行时间差在该秒数内才显示，否则清空，避免“上一句翻译残留”
TRANSLATION_MATCH_WINDOW = 0.6

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

        # 逐字渐变开关（系统托盘控制）
        self.karaoke_enabled = True

        # 锁定状态
        self.is_locked = False

        # UI
        self._build_ui()

        # 消息队列
        self.message_queue = queue.Queue()
        self.root.after(100, self.process_queue)

        # 托盘
        self.tray_icon = None
        self._create_tray_icon()

        # 连接集
        self.connected_clients = set()

        # 拖动
        self.drag_data = {"x": 0, "y": 0, "dragging": False}

        # 播放时钟
        self._last_sync_time = 0.0
        self._last_sync_mono = time.perf_counter()

        # 布局和渲染缓存
        self.current_line_start = 0.0
        self.next_line_start = 0.0
        self._need_layout = True
        self._line_positions = []     # [(ch, x), ...]
        self._line_width = 0
        self._char_items = []         # 主字 item id 列表（卡拉OK模式或整行模式的主文本）
        self._outline_items = []      # 每字/整行的描边 id 列表
        self._trans_item = None       # 翻译主 item
        self._trans_outline_items = []# 翻译描边 items

        # 动画循环（自适应帧率）
        self.root.after(self._frame_delay_ms(IDLE_FPS), self.animation_tick)

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

        # 保留对象但不 pack；如需用 label 显示翻译，改为 pack()
        self.translation_label = tk.Label(self.main_frame, text="", font=self.translation_font,
                                          fg=TRANSLATION_FG, bg=BG_COLOR, pady=8)

        # 鼠标事件
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
        self.is_locked = not self.is_locked
        if self.is_locked:
            self._change_bg(BG_COLOR)
            self.root.attributes("-alpha", WINDOW_ALPHA)
        self._update_tray_menu()

    def _toggle_karaoke(self, *_):
        # 切换逐字渐变开关并重建当前行的绘制项
        self.karaoke_enabled = not self.karaoke_enabled
        self._need_layout = True
        self._prepare_line_layout()
        self._rebuild_items()
        self._update_tray_menu()

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
            self.tray_icon.update_menu()

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
        self._disconnect_client()
        self.root.after(100, self.root.destroy)
        if self.tray_icon:
            self.tray_icon.stop()

    # 连接状态
    def update_status(self, status):
        self.connection_status = status
        if status == "connected":
            self.song_label.config(text="已连接 - 等待歌曲...", fg=SONG_FG)
        elif status == "disconnected":
            self.song_label.config(text="等待连接...", fg=SONG_FG)

    # 解析
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

    # 布局和缓存
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
        # 逐字模式下，仍逐字测宽；普通模式只用整行宽居中
        widths = [self.lyric_font.measure(ch) for ch in s]
        total_w = self.lyric_font.measure(s) if not self.karaoke_enabled else sum(widths)
        x = (canvas_w - total_w) // 2
        pos = []
        if self.karaoke_enabled:
            for ch, w in zip(s, widths):
                pos.append((ch, x))
                x += w
        else:
            pos = []
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
        # 删除占位提示（“正在加载歌词…”等）
        try:
            self.lyric_canvas.delete("placeholder")
        except Exception:
            pass

    def _rebuild_items(self):
        # 先清掉占位提示，避免与实际歌词叠屏
        self._clear_placeholder()

        # 删除上一行的 item，只在换行/窗口尺寸变化/开关切换时做
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
            # 主行：逐字（描边固定，主字颜色随帧变化）
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
            # 主行：整行静态（普通展示）
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

        # 翻译（静态）
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

        # 主歌词：确定当前行
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

        # 翻译：与“当前主行时间”对齐，仅在接近时显示，否则清空
        # 这样可避免在无翻译的行显示上一句翻译残留
        if self.translations_data and current_index != -1:
            target_t = self.lyrics_data[current_index]['time']
            best_idx = -1
            best_diff = 1e9
            # 遍历匹配最近的翻译时间（可优化为二分，这里行数有限直接遍历）
            for i, tline in enumerate(self.translations_data):
                diff = abs(tline['time'] - target_t)
                if diff < best_diff:
                    best_diff = diff
                    best_idx = i
                # 小优化：翻译时间超过目标太多可提前结束
                if tline['time'] - target_t > TRANSLATION_MATCH_WINDOW:
                    break
            # 是否在窗口内
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

        # 如需，重建 items
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
        # 逐字渐变关闭时，无需高帧率
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
        # 计算“当前应显示的时间”（若暂停则冻结）
        now = self._now_playback_time()
        # 更新逻辑（只在换行/翻译变更/窗口变化时重建 items）
        self.update_lyrics_with_time(now)

        # 每帧仅在开启逐字渐变时更新主字颜色
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

        # 自适应帧率：暂停/静止降帧；关闭卡拉OK时也按 idle 帧率
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
                    # 清空画布和缓存
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
                    # 立即对齐一帧，避免视觉滞后
                    self.update_lyrics_with_time(self._now_playback_time())
                elif msg_type == "clear":
                    # 清空所有
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
            print(f"收到歌词: {len(self.lyrics_data)} 行, 翻译: {len(self.translations_data)} 行")
            # 收到整首歌词后，清理占位提示，避免叠屏
            self._clear_placeholder()
            if not self.has_lyrics:
                self._draw_center_text("当前歌曲无歌词/正在等待网页传输", LYRIC_FG)
        except Exception as e:
            print(f"解析歌词时出错: {e}")
            self._draw_center_text("歌词解析错误", LYRIC_FG)

    # 辅助绘制
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

    # 运行
    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            self._quit()
        except Exception as e:
            print(f"主循环错误: {e}")
            self._quit()

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
    desktop_lyrics = DesktopLyrics()
    server_thread = threading.Thread(target=start_websocket_server, args=(desktop_lyrics,), daemon=True)
    server_thread.start()
    desktop_lyrics.run()
    print("程序已退出")
