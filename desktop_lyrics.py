import asyncio
import websockets
import tkinter as tk
from tkinter import messagebox
from tkinter import ttk  # 新增：引入ttk用于进度条
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
from colorsys import hls_to_rgb
import os
import sys

# ============ 新增：依赖库检查 ============
try:
    import requests
except ImportError:
    print("❌ 未找到 requests 库，更新功能将不可用。请运行: pip install requests")
    requests = None

# ============ 音频库导入和错误处理 ============
AUDIO_AVAILABLE = False
PA = None

try:
    import pyaudiowpatch as pyaudio
    PA = pyaudio
    AUDIO_AVAILABLE = True
    print("✅ [develop]成功导入 pyaudiowpatch，系统音频捕获可用")
except ImportError:
    print("⚠️  [develop]未找到 pyaudiowpatch，尝试导入标准 PyAudio...")
    try:
        import pyaudio
        PA = pyaudio
        AUDIO_AVAILABLE = True
        print("✅ [develop]成功导入标准 PyAudio，麦克风输入可用")
    except ImportError:
        print("❌ [develop]未找到 PyAudio，音频功能将不可用")
        print("安装命令: pip install pyaudiowpatch")
        AUDIO_AVAILABLE = False
        PA = None
except Exception as e:
    print(f"❌ 导入音频库时出错: {e}")
    AUDIO_AVAILABLE = False
    PA = None

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

# 卡拉OK参数（优化后）
MAX_FPS_MOVING = 60  # 请根据您的设备刷新率设置
IDLE_FPS = 10
PAUSED_FPS = 2
KARAOKE_FADE_TIME = 0.25  # 从0.30优化到0.25
KARAOKE_HL_COLOR = "#FFD700"
KARAOKE_SHIMMER = 0.0
LAST_LINE_FALLBACK = 3.0
OUTLINE_SIZE = 1
OUTLINE_COLOR = "#000000"
OUTLINE_NEIGHBORS = 4  # 从8优化到4
TIME_FREEZE_ON_STALE_SEC = 0.8
RENDER_TRANSLATION_ON_CANVAS = True
TRANSLATION_TOP_GAP = 8
TRANSLATION_MATCH_WINDOW = 0.6

# 透明色键
TRANSPARENT_KEY = "#FF00FF"

# 颜色LUT步进数
COLOR_LUT_STEPS = 100
SHIMMER_LUT_STEPS = 50

# ============ 新增：更新管理模块（带进度条） ============
CURRENT_VERSION = "v0.1.0"  # 【重要】发布新版前，必须修改此处的版本号！
REPO_OWNER = "beststoryilove"
REPO_NAME = "Harmonia-DesktopLyrics"
UPDATE_API_URL = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/releases/latest"

class UpdateManager:
    def __init__(self, root):
        self.root = root
        self.check_window = None

    def start_check(self):
        """启动更新检查"""
        if requests is None:
            return
        
        # 弹窗显示正在检查
        self.check_window = tk.Toplevel(self.root)
        self.check_window.title("检查更新")
        self.check_window.geometry("300x100")
        self.center_window(self.check_window)
        self.check_window.transient(self.root)
        self.check_window.grab_set() # 模态
        
        tk.Label(self.check_window, text="正在检查更新中...", font=("Microsoft YaHei UI", 12)).pack(expand=True)
        
        threading.Thread(target=self._check_thread, daemon=True).start()

    def _parse_version(self, v_str):
        """将版本字符串解析为数字元组，例如 'v0.1.2' -> (0, 1, 2)"""
        try:
            # 移除常见前缀
            clean_ver = v_str.lower().replace("updata", "").replace("v", "").strip()
            # 分割并转为整数
            return tuple(int(x) for x in clean_ver.split("."))
        except Exception:
            return (0, 0, 0)

    def _check_thread(self):
        try:
            response = requests.get(UPDATE_API_URL, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            latest_tag = data.get("tag_name", "") # e.g., updatav0.1.1
            body = data.get("body", "暂无更新日志")
            
            # 解析版本号
            remote_ver_str = latest_tag.replace("updata", "").strip()
            
            # === 关键修复：语义化版本比对 ===
            local_ver = self._parse_version(CURRENT_VERSION)
            remote_ver = self._parse_version(remote_ver_str)
            
            print(f"[Update] Local: {local_ver}, Remote: {remote_ver}") # 调试输出

            # 关闭检查窗口
            self.root.after(0, self.check_window.destroy)
            
            # 只有当 远程版本 > 当前版本 时才提示更新
            if remote_ver > local_ver:
                self.root.after(0, lambda: self._show_update_dialog(latest_tag, remote_ver_str, body))
            else:
                # 远程版本 <= 当前版本（包含相同或者是开发版更新的情况），视为无更新
                self.root.after(0, lambda: self._show_no_update_dialog(remote_ver_str))
                
        except Exception as e:
            print(f"检查更新失败: {e}")
            self.root.after(0, self.check_window.destroy)
            # 网络错误可以选择静默失败，或者提示用户
            # self.root.after(0, lambda: messagebox.showerror("检查失败", f"网络错误: {e}"))

    def _show_update_dialog(self, tag, version_part, body):
        msg = f"发现新版本！\n\n当前版本: {CURRENT_VERSION}\n最新版本: {version_part}\n\n更新日志：\n{body}\n\n是否立即更新？"
        if messagebox.askyesno("发现新版本", msg, parent=self.root):
            self._download_update(tag, version_part)

    def _show_no_update_dialog(self, remote_version):
        # 提示用户当前已是最新
        msg = f"您正在使用最新版，欢迎使用Harmonia桌面歌词！\n\n当前版本: {CURRENT_VERSION}\n(远程版本: {remote_version})"
        messagebox.showinfo("检查完成", msg, parent=self.root)

    def _download_update(self, tag, version_part):
        filename = f"Harmonia-DesktopLyrics-{version_part}.exe"
        url = f"https://github.com/{REPO_OWNER}/{REPO_NAME}/releases/download/{tag}/{filename}"
        save_path = os.path.join(os.path.expanduser("~"), "Downloads", filename)
        
        # === 进度条下载窗口 ===
        progress_win = tk.Toplevel(self.root)
        progress_win.title("正在下载更新")
        progress_win.geometry("400x150")
        self.center_window(progress_win)
        progress_win.transient(self.root)
        progress_win.grab_set()
        progress_win.protocol("WM_DELETE_WINDOW", lambda: None) # 禁止关闭

        tk.Label(progress_win, text=f"正在下载: {filename}", font=("Microsoft YaHei UI", 10)).pack(pady=(20, 10))
        
        progress_var = tk.DoubleVar()
        pb = ttk.Progressbar(progress_win, variable=progress_var, maximum=100)
        pb.pack(fill="x", padx=30, pady=5)
        
        percent_label = tk.Label(progress_win, text="准备开始...", font=("Microsoft YaHei UI", 9), fg="#666666")
        percent_label.pack(pady=5)

        threading.Thread(target=self._download_file_thread, 
                         args=(url, save_path, progress_win, progress_var, percent_label), 
                         daemon=True).start()

    def _download_file_thread(self, url, save_path, window, progress_var, percent_label):
        try:
            with requests.get(url, stream=True, timeout=30) as r:
                r.raise_for_status()
                total_length = r.headers.get('content-length')

                with open(save_path, 'wb') as f:
                    if total_length is None: 
                        f.write(r.content)
                        self.root.after(0, lambda: self._update_progress_ui(progress_var, percent_label, 100))
                    else:
                        dl = 0
                        total_length = int(total_length)
                        for chunk in r.iter_content(chunk_size=8192):
                            if chunk:
                                dl += len(chunk)
                                f.write(chunk)
                                percent = (dl / total_length) * 100
                                self.root.after(0, lambda p=percent: self._update_progress_ui(progress_var, percent_label, p))
            
            self.root.after(0, window.destroy)
            self.root.after(0, lambda: messagebox.showinfo("下载完成", f"下载成功！\n请前往下载目录运行新版本：\n{save_path}", parent=self.root))
            
        except Exception as e:
            self.root.after(0, window.destroy)
            self.root.after(0, lambda: messagebox.showerror("下载失败", f"下载出错: {e}", parent=self.root))

    def _update_progress_ui(self, var, label, percent):
        var.set(percent)
        label.config(text=f"{percent:.1f}%")

    def center_window(self, win):
        win.update_idletasks()
        width = win.winfo_width()
        height = win.winfo_height()
        x = (win.winfo_screenwidth() // 2) - (width // 2)
        y = (win.winfo_screenheight() // 2) - (height // 2)
        win.geometry(f'+{x}+{y}')

# ============ 新增：视觉特效类 ============
class VisualEffects:
    """视觉特效管理器"""
    
    @staticmethod
    def gradient_color(start_color, end_color, steps):
        """生成渐变颜色列表"""
        start_rgb = tuple(int(start_color[i:i+2], 16) for i in (1, 3, 5))
        end_rgb = tuple(int(end_color[i:i+2], 16) for i in (1, 3, 5))
        
        colors = []
        for i in range(steps):
            r = int(start_rgb[0] + (end_rgb[0] - start_rgb[0]) * i / steps)
            g = int(start_rgb[1] + (end_rgb[1] - start_rgb[1]) * i / steps)
            b = int(start_rgb[2] + (end_rgb[2] - start_rgb[2]) * i / steps)
            colors.append(f"#{r:02x}{g:02x}{b:02x}")
        return colors
    
    @staticmethod
    def rainbow_color(position):
        """彩虹色生成器"""
        r = int(255 * abs(math.sin(position)))
        g = int(255 * abs(math.sin(position + math.pi/3)))
        b = int(255 * abs(math.sin(position + 2*math.pi/3)))
        return f"#{r:02x}{g:02x}{b:02x}"
    
    @staticmethod
    def pulse_color(base_color, intensity):
        """脉冲颜色效果"""
        r, g, b = tuple(int(base_color[i:i+2], 16) for i in (1, 3, 5))
        r = min(255, int(r * (1 + intensity * 0.3)))
        g = min(255, int(g * (1 + intensity * 0.3)))
        b = min(255, int(b * (1 + intensity * 0.3)))
        return f"#{r:02x}{g:02x}{b:02x}"
    
    @staticmethod
    def hsl_color(hue, saturation=0.8, lightness=0.7):
        """HSL颜色生成"""
        r, g, b = [int(c * 255) for c in hls_to_rgb(hue, lightness, saturation)]
        return f"#{r:02x}{g:02x}{b:02x}"

# ------- Windows 任务栏定位 -------
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

# ============ 改进的音频线程 ============
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
        
        # 音频设备
        self.p = None
        self.stream = None
        self.rate = 48000
        self.chunk = 2048
        self.band_idx = None
        self.freqs = None
        
        # 显示数据
        self.display_levels = np.zeros(self.num_bars, dtype=np.float32)
        
        # 限流控制
        self._update_throttle = 0.033  # 最多30Hz更新UI
        self._last_update = 0.0
        
        # 模拟模式标志
        self.simulation_mode = False
        self.simulation_time = 0.0
        self.simulation_freq = 0.0

    def _open_audio_stream(self):
        """尝试打开音频流，支持多种回退方案"""
        if not AUDIO_AVAILABLE or PA is None:
            print("⚠️  [develop]音频库不可用，启用模拟模式")
            self.simulation_mode = True
            return False
        
        try:
            self.p = PA.PyAudio()
            print(f"✅ [develop]成功初始化 PyAudio，版本: {PA.__version__}")
            
            # 列出所有可用设备
            print("\n=== 可用音频设备 ===")
            for i in range(self.p.get_device_count()):
                try:
                    dev_info = self.p.get_device_info_by_index(i)
                    print(f"[{i}] {dev_info['name']}")
                    print(f"   输入通道: {dev_info['maxInputChannels']}, 输出通道: {dev_info['maxOutputChannels']}")
                    print(f"   默认采样率: {dev_info['defaultSampleRate']}")
                except:
                    pass
            print("===================\n")
            
            # 尝试多种打开方式
            stream_methods = [
                self._try_wasapi_loopback,
                self._try_default_input,
                self._try_any_input_device
            ]
            
            for method in stream_methods:
                try:
                    stream = method()
                    if stream:
                        print(f"✅ [develop]成功使用 {method.__name__} 打开音频流")
                        self.stream = stream
                        return True
                except Exception as e:
                    print(f"⚠️  {method.__name__} 失败: {e}")
                    continue
            
            print("❌ [develop]所有音频打开方式都失败，启用模拟模式")
            self.simulation_mode = True
            return False
            
        except Exception as e:
            print(f"❌ [develop]PyAudio 初始化失败: {e}")
            self.simulation_mode = True
            return False

    def _try_wasapi_loopback(self):
        """尝试使用WASAPI回环捕获系统音频"""
        try:
            wasapi_info = self.p.get_host_api_info_by_type(PA.paWASAPI)
            print(f"✅ [develop]检测到 WASAPI，默认输出设备索引: {wasapi_info['defaultOutputDevice']}")
            
            # 获取默认输出设备信息
            default_out = self.p.get_device_info_by_index(wasapi_info["defaultOutputDevice"])
            rate = int(default_out.get("defaultSampleRate", 48000)) or 48000
            
            print(f"📊 [develop]设备信息:")
            print(f"   名称: {default_out['name']}")
            print(f"   采样率: {rate} Hz")
            print(f"   输出通道数: {default_out.get('maxOutputChannels', 2)}")
            
            # 尝试直接打开回环流
            try:
                stream = self.p.open(
                    format=PA.paInt16,
                    channels=min(2, int(default_out.get("maxOutputChannels", 2))),
                    rate=rate,
                    input=True,
                    frames_per_buffer=self.chunk,
                    input_device_index=default_out["index"],
                    as_loopback=True
                )
                self.rate = rate
                return stream
            except Exception as e:
                print(f"⚠️  [develop]直接回环失败: {e}")
                
            # 尝试查找回环设备
            print("🔍 [develop]搜索回环设备...")
            for i in range(self.p.get_device_count()):
                try:
                    dev_info = self.p.get_device_info_by_index(i)
                    if "loopback" in dev_info['name'].lower() or "立体声混音" in dev_info['name']:
                        print(f"✅ [develop]找到回环设备: {dev_info['name']}")
                        rate = int(dev_info["defaultSampleRate"])
                        channels = min(2, int(dev_info.get("maxInputChannels", 2)))
                        
                        stream = self.p.open(
                            format=PA.paInt16,
                            channels=channels,
                            rate=rate,
                            input=True,
                            frames_per_buffer=self.chunk,
                            input_device_index=i
                        )
                        self.rate = rate
                        return stream
                except:
                    continue
                    
            return None
            
        except Exception as e:
            print(f"⚠️  [develop]WASAPI 检测失败: {e}")
            return None

    def _try_default_input(self):
        """尝试打开默认输入设备"""
        try:
            # 获取默认输入设备
            default_input = self.p.get_default_input_device_info()
            rate = int(default_input.get("defaultSampleRate", 48000))
            channels = min(2, int(default_input.get("maxInputChannels", 1)))
            
            print(f"🎤 [develop]使用默认输入设备: {default_input['name']}")
            print(f"   [develop]采样率: {rate} Hz, 通道数: {channels}")
            
            stream = self.p.open(
                format=PA.paInt16,
                channels=channels,
                rate=rate,
                input=True,
                frames_per_buffer=self.chunk,
                input_device_index=default_input["index"]
            )
            self.rate = rate
            return stream
            
        except Exception as e:
            print(f"⚠️  [develop]默认输入设备失败: {e}")
            return None

    def _try_any_input_device(self):
        """尝试打开任何可用的输入设备"""
        try:
            for i in range(self.p.get_device_count()):
                try:
                    dev_info = self.p.get_device_info_by_index(i)
                    if dev_info.get("maxInputChannels", 0) > 0:
                        rate = int(dev_info.get("defaultSampleRate", 48000))
                        channels = min(2, int(dev_info.get("maxInputChannels", 1)))
                        
                        print(f"🔊 [develop]尝试输入设备 [{i}]: {dev_info['name']}")
                        
                        stream = self.p.open(
                            format=PA.paInt16,
                            channels=channels,
                            rate=rate,
                            input=True,
                            frames_per_buffer=self.chunk,
                            input_device_index=i
                        )
                        self.rate = rate
                        return stream
                except Exception as e:
                    continue
                    
            return None
            
        except Exception as e:
            print(f"⚠️  [develop]所有输入设备尝试失败: {e}")
            return None

    def _prepare_fft_bands(self):
        """准备FFT频带"""
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

    def _generate_simulation_data(self):
        """生成模拟音频数据"""
        self.simulation_time += 0.05
        self.simulation_freq = 5.0 + 4.0 * math.sin(self.simulation_time * 0.3)
        
        # 生成随机频谱数据
        np.random.seed(int(self.simulation_time * 10))
        base = np.random.randn(self.num_bars) * 0.3
        
        # 添加正弦波模式
        for i in range(self.num_bars):
            freq = 0.1 + 0.9 * (i / self.num_bars)
            base[i] += 0.5 * math.sin(self.simulation_time * freq * self.simulation_freq)
        
        # 归一化到0-1范围
        levels = (base - base.min()) / (base.max() - base.min() + 1e-10)
        
        # 添加随机峰值
        if np.random.random() < 0.1:
            peak_pos = np.random.randint(0, self.num_bars)
            levels[peak_pos] = 1.0
        
        return levels.astype(np.float32)

    def run(self):
        """音频处理主循环"""
        # 尝试打开音频设备
        if not self._open_audio_stream():
            print("🎵 [develop]进入模拟模式，律动条将显示模拟波形")
            self.simulation_mode = True
        
        # 如果是真实音频模式，准备FFT
        if not self.simulation_mode and self.stream:
            self._prepare_fft_bands()
            window = np.hanning(self.chunk).astype(np.float32)
        
        print("▶️  [develop]开始音频处理循环...")
        
        while not self.stop_event.is_set():
            try:
                if self.simulation_mode:
                    # 模拟模式：生成模拟数据
                    levels = self._generate_simulation_data()
                    self.display_levels = levels
                    
                    # 限流控制
                    now_t = time.perf_counter()
                    if now_t - self._last_update >= self._update_throttle:
                        self._last_update = now_t
                        self.on_levels(self.display_levels.copy())
                    
                    time.sleep(self._update_throttle)
                    
                elif self.stream:
                    # 真实音频模式
                    try:
                        buf = self.stream.read(self.chunk, exception_on_overflow=False)
                    except Exception as e:
                        print(f"⚠️  [develop]读取音频流失败: {e}")
                        time.sleep(0.1)
                        continue
                    
                    data = np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
                    
                    # 处理多声道音频
                    if getattr(self.stream, "_channels", 1) >= 2:
                        try:
                            data = data.reshape(-1, self.stream._channels).mean(axis=1)
                        except Exception:
                            pass
                    
                    x = data[:self.chunk] * window
                    spec = np.fft.rfft(x)
                    mag = np.abs(spec) + 1e-10
                    db = 20.0 * np.log10(mag)
                    
                    # 计算频带值
                    band_vals = np.empty(self.num_bars, dtype=np.float32)
                    for i, sel in enumerate(self.band_idx):
                        band_vals[i] = db[sel].max()
                    
                    levels = (band_vals - self.min_db) / (self.max_db - self.min_db)
                    levels = np.clip(levels, 0.0, 1.0)
                    
                    # 平滑处理
                    prev = self.display_levels
                    up = np.maximum(levels, prev * (1.0 - self.peak_decay))
                    smoothed = self.smooth_alpha * prev + (1.0 - self.smooth_alpha) * up
                    self.display_levels = smoothed
                    
                    # 限流控制
                    now_t = time.perf_counter()
                    if now_t - self._last_update >= self._update_throttle:
                        self._last_update = now_t
                        self.on_levels(self.display_levels.copy())
                        
                else:
                    # 无可用音频设备
                    time.sleep(0.1)
                    
            except Exception as e:
                print(f"⚠️  [develop]音频处理异常: {e}")
                time.sleep(0.1)
        
        # 清理资源
        self._cleanup()

    def _cleanup(self):
        """清理音频资源"""
        print("🧹 [develop]清理音频资源...")
        try:
            if self.stream and hasattr(self.stream, 'is_active') and self.stream.is_active():
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

# ============ 优化后的律动条 ============
class VisualizerOverlay:
    def __init__(self, root):
        self.root = root
        self.win = tk.Toplevel(self.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=TRANSPARENT_KEY)
        try:
            self.win.attributes("-transparentcolor", TRANSPARENT_KEY)
        except Exception:
            pass

        edge, tb_rect = _detect_taskbar_edge()
        screen_w, screen_h = _get_screen_size()
        
        # 优化参数
        self.strip_height_px = 80  # 增加高度
        self.side_strip_px = 140
        self.bar_spacing_px = 1  # 减小间距
        self.min_bar_px = 2
        
        # 动态颜色参数
        self.color_mode = "gradient"  # gradient, rainbow, pulse, single
        self.base_color = "#00ff7f"
        self.gradient_colors = VisualEffects.gradient_color("#00ff7f", "#ff007f", 100)
        self.rainbow_offset = 0.0
        self.pulse_phase = 0.0
        
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
        self.canvas = tk.Canvas(self.win, width=win_w, height=win_h,
                                bg=TRANSPARENT_KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.win.update_idletasks()
        self._apply_click_through_and_colorkey()

        # 计算条形数量和大小
        if not self.vertical_layout:
            full = self.min_bar_px + self.bar_spacing_px
            self.num_bars = min(max(80, win_w // max(1, full)), 320)  # 增加数量
            total_spacing = (self.num_bars + 1) * self.bar_spacing_px
            avail = max(1, win_w - total_spacing)
            self.bar_w = max(self.min_bar_px, avail // self.num_bars)
            self.bar_h = win_h
        else:
            full = self.min_bar_px + self.bar_spacing_px
            self.num_bars = min(max(80, win_h // max(1, full)), 320)
            total_spacing = (self.num_bars + 1) * self.bar_spacing_px
            avail = max(1, win_h - total_spacing)
            self.bar_w = max(self.min_bar_px, (win_w - 2 * self.bar_spacing_px))
            self.bar_h = max(self.min_bar_px, avail // self.num_bars)

        # 创建条形
        self.bars = []
        self.glow_bars = []  # 发光效果层
        
        if not self.vertical_layout:
            for i in range(self.num_bars):
                x1 = self.bar_spacing_px + i * (self.bar_w + self.bar_spacing_px)
                x2 = x1 + self.bar_w
                y2 = win_h
                y1 = y2
                # 发光层
                glow = self.canvas.create_rectangle(
                    x1 - 1, y1 - 1, x2 + 1, y2 + 1,
                    fill="#4A90E2", width=0, state='hidden'
                )
                self.glow_bars.append(glow)
                # 主条形
                r = self.canvas.create_rectangle(x1, y1, x2, y2, 
                                                 fill=self.base_color, width=0)
                self.bars.append(r)
        else:
            for i in range(self.num_bars):
                y1 = self.bar_spacing_px + i * (self.bar_h + self.bar_spacing_px)
                y2 = y1 + self.bar_h
                x1 = 0
                x2 = 0
                # 发光层
                glow = self.canvas.create_rectangle(
                    x1 - 1, y1 - 1, x2 + 1, y2 + 1,
                    fill="#4A90E2", width=0, state='hidden'
                )
                self.glow_bars.append(glow)
                # 主条形
                r = self.canvas.create_rectangle(x1, y1, x2, y2, 
                                                 fill=self.base_color, width=0)
                self.bars.append(r)

        # 音频处理
        self._stop_evt = threading.Event()
        self.worker = None
        self.thread = None
        self._running = False
        
        # 历史数据用于平滑
        self.last_levels = np.zeros(self.num_bars, dtype=np.float32)
        self.peak_levels = np.zeros(self.num_bars, dtype=np.float32)
        
        # 尝试启动音频
        self._start_audio()
            
        self.alive = True
        self._visible = True
        
        # 启动颜色动画
        self.rainbow_speed = 0.02
        self.pulse_speed = 0.05
        self._animate_colors()

    def _animate_colors(self):
        """颜色动画循环"""
        if self.alive and self._visible:
            self.rainbow_offset = (self.rainbow_offset + self.rainbow_speed) % 1.0
            self.pulse_phase = (self.pulse_phase + self.pulse_speed) % (2 * math.pi)
        self.win.after(50, self._animate_colors)

    def _get_bar_color(self, i, level):
        """根据模式和位置获取条形颜色"""
        if self.color_mode == "gradient":
            color_idx = min(int(level * (len(self.gradient_colors) - 1)), 
                          len(self.gradient_colors) - 1)
            return self.gradient_colors[color_idx]
        elif self.color_mode == "rainbow":
            position = (i / self.num_bars + self.rainbow_offset) % 1.0
            return VisualEffects.rainbow_color(position * 2 * math.pi)
        elif self.color_mode == "pulse":
            intensity = (math.sin(self.pulse_phase) + 1) * 0.5
            return VisualEffects.pulse_color(self.base_color, intensity * level)
        else:
            return self.base_color

    def _update_bars(self, levels):
        if not self.alive or not self._visible:
            return
            
        # 应用平滑
        smooth_levels = 0.7 * levels + 0.3 * self.last_levels
        self.last_levels = smooth_levels
        
        # 更新峰值
        self.peak_levels = np.maximum(smooth_levels * 0.9, self.peak_levels * 0.98)
        
        canvas_w = int(self.canvas.winfo_width())
        canvas_h = int(self.canvas.winfo_height())
        
        if not self.vertical_layout:
            for i, lv in enumerate(smooth_levels):
                # 主条形高度
                bh = int(lv * canvas_h * 1.1)  # 增加幅度
                x1 = self.bar_spacing_px + i * (self.bar_w + self.bar_spacing_px)
                x2 = x1 + self.bar_w
                y2 = canvas_h
                y1 = max(0, y2 - bh)
                
                # 峰值条形（半透明）
                peak_h = int(self.peak_levels[i] * canvas_h)
                peak_y1 = max(0, y2 - peak_h)
                
                # 更新主条形
                self.canvas.coords(self.bars[i], x1, y1, x2, y2)
                
                # 设置颜色
                color = self._get_bar_color(i, lv)
                self.canvas.itemconfig(self.bars[i], fill=color)
                
                # 控制发光效果
                if lv > 0.7:
                    self.canvas.itemconfig(self.glow_bars[i], state='normal')
                    glow_y1 = max(0, y2 - bh - 2)
                    self.canvas.coords(self.glow_bars[i], 
                                      x1 - 2, glow_y1, x2 + 2, y2 + 2)
                else:
                    self.canvas.itemconfig(self.glow_bars[i], state='hidden')
        else:
            for i, lv in enumerate(smooth_levels):
                bw = int(lv * canvas_w * 1.1)
                y1 = self.bar_spacing_px + i * (self.bar_h + self.bar_spacing_px)
                y2 = y1 + self.bar_h
                x2 = canvas_w
                x1 = max(0, x2 - bw)
                
                # 更新主条形
                self.canvas.coords(self.bars[i], x1, y1, x2, y2)
                
                # 设置颜色
                color = self._get_bar_color(i, lv)
                self.canvas.itemconfig(self.bars[i], fill=color)
                
                # 控制发光效果
                if lv > 0.7:
                    self.canvas.itemconfig(self.glow_bars[i], state='normal')
                    glow_x1 = max(0, x2 - bw - 2)
                    self.canvas.coords(self.glow_bars[i], 
                                      glow_x1, y1 - 2, x2 + 2, y2 + 2)
                else:
                    self.canvas.itemconfig(self.glow_bars[i], state='hidden')

    def _apply_click_through_and_colorkey(self):
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
        self._apply_click_through_to_all_children(self.win.winfo_id())

    def _apply_click_through_to_all_children(self, parent_hwnd):
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
            EnumChildWindows(parent_hwnd, _enum_proc, 0)
        except Exception:
            pass

    def _start_audio(self):
        if self._running or not AUDIO_AVAILABLE:
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

    def show(self):
        self.win.deiconify()
        self.win.lift()
        self._apply_click_through_and_colorkey()
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

# ------- 歌词主窗口（优化版）-------
class DesktopLyrics:
    TIME_TAG_RE = re.compile(r"\[(\d{1,2}):(\d{1,2})(?:[.:](\d{1,3}))?\]")

    def __init__(self):
        self.root = tk.Tk()
        self.root.title(f"Harmonia桌面歌词 - {CURRENT_VERSION}")
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

        # 🔥 优化：布局缓存 + 智能脏标记
        self.current_line_start = 0.0
        self.next_line_start = 0.0
        self._layout_dirty = True
        self._items_dirty = True
        self._last_lyric_hash = None
        self._line_positions = []
        self._line_width = 0
        self._char_items = []
        self._outline_items = []
        self._trans_item = None
        self._trans_outline_items = []

        # 🔥 优化：预计算颜色LUT
        self._color_lut = self._build_color_lut(LYRIC_FG, KARAOKE_HL_COLOR, COLOR_LUT_STEPS)
        self._shimmer_lut = self._build_color_lut(KARAOKE_HL_COLOR, "#FFFFFF", SHIMMER_LUT_STEPS)

        # 动画循环
        self.root.after(self._frame_delay_ms(IDLE_FPS), self.animation_tick)

        # 启动律动条
        if self.visualizer_enabled:
            try:
                self.visualizer = VisualizerOverlay(self.root)
                self.visualizer.show()
            except Exception as e:
                print(f"创建律动条失败：{e}")
                print("律动条将不可用，但歌词功能正常")
                self.visualizer = None
                self.visualizer_enabled = False

        # =========================================
        # 集成更新检查
        # =========================================
        self.updater = UpdateManager(self.root)
        # 延迟1秒启动检查，避免影响主界面启动
        self.root.after(1000, self.updater.start_check)

    def _build_fonts(self):
        self.lyric_font = tkfont.Font(family=FONT_NAME, size=LYRIC_FONT_SIZE, weight="bold")
        self.translation_font = tkfont.Font(family=FONT_NAME, size=TRANSLATION_FONT_SIZE, weight="normal")
        self.song_font = tkfont.Font(family=FONT_NAME, size=SONG_FONT_SIZE, weight="bold")

    def _build_ui(self):
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        self.song_label = tk.Label(self.main_frame, text="等待连接... Harmonia桌面歌词", font=self.song_font,
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

    # 🔥 优化：预计算颜色LUT
    def _build_color_lut(self, color_a, color_b, steps):
        """预计算颜色渐变查找表"""
        a = self._hex_to_rgb(color_a)
        b = self._hex_to_rgb(color_b)
        lut = []
        for i in range(steps + 1):
            t = i / steps
            eased_t = t * t * (3 - 2 * t)  # 预先计算缓动函数
            rgb = tuple(int(round(a[j] + (b[j] - a[j]) * eased_t)) for j in range(3))
            lut.append(self._rgb_to_hex(rgb))
        return lut

    def _get_color_from_lut(self, progress, lut):
        """从LUT快速获取颜色（零计算）"""
        progress = max(0.0, min(1.0, progress))
        idx = int(progress * (len(lut) - 1))
        return lut[idx]

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
            self._layout_dirty = True
            self._items_dirty = True
            self._prepare_line_layout()
            self._rebuild_items()
            self._update_tray_menu()
        self.root.after(0, _do)

    def _toggle_visualizer(self, *_):
        def _do():
            self.visualizer_enabled = not self.visualizer_enabled
            if self.visualizer_enabled:
                if self.visualizer is None:
                    try:
                        self.visualizer = VisualizerOverlay(self.root)
                        self.visualizer.show()
                    except Exception as e:
                        print(f"创建律动条失败：{e}")
                        self.visualizer = None
                        self.visualizer_enabled = False
                else:
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
                # 新增的提示按钮
                pystray.MenuItem("温馨提示：下面两个功能以视觉为主，由于屎山代码，因此并没有什么优化，请谨慎开启", 
                                lambda _: None, enabled=False),
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
            self.song_label.config(text="已连接至网页 - 等待传输歌曲... Harmonia桌面歌词", fg=SONG_FG)
        elif status == "disconnected":
            self.song_label.config(text="等待连接... Harmonia桌面歌词", fg=SONG_FG)

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

    # 🔥 优化：智能脏标记
    def invalidate_layout(self):
        """仅标记脏，不立即处理"""
        self._layout_dirty = True

    def _prepare_line_layout(self):
        s = self.current_lyric or ""
        if not s:
            self._line_positions = []
            self._line_width = 0
            self._layout_dirty = False
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
        self._layout_dirty = False

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

    # 🔥 优化：逻辑更新 + 智能脏标记
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
            line_changed = True

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

        # 🔥 仅在内容真正变化时重建
        new_hash = hash((self.current_lyric, self.current_translation))
        if new_hash != self._last_lyric_hash:
            self._last_lyric_hash = new_hash
            self._layout_dirty = True
            self._items_dirty = True

        if self._layout_dirty:
            self._prepare_line_layout()
            self._layout_dirty = False
            self._items_dirty = True

        if self._items_dirty:
            self._rebuild_items()
            self._items_dirty = False

    # 🔥 优化：渲染使用LUT + 批量更新
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

    # 🔥 优化：更智能的动画检测
    def _any_char_animating(self, now):
        """优化动画检测：提前退出"""
        if not self.karaoke_enabled or not self._char_items or not self.current_lyric:
            return False

        s = self.current_lyric
        start_t = self.current_line_start
        end_t = max(start_t + 0.01, self.next_line_start)

        # 如果整行已完成，直接返回
        if now > end_t + KARAOKE_FADE_TIME:
            return False

        # 如果还未开始，直接返回
        if now < start_t:
            return False

        # 在过渡区间内
        return True

    def _frame_delay_ms(self, fps):
        """更智能的FPS调度"""
        fps = max(1, min(fps, 144))
        return int(1000 / fps)

    # 🔥 优化：动画循环使用LUT
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
            do_shimmer = KARAOKE_SHIMMER > 0.0

            # 🔥 批量收集更新操作
            updates = []
            for i, mid in enumerate(self._char_items):
                ch_start = start_t + i * char_delay
                p = (now - ch_start) / fade_t
                p = max(0.0, min(1.0, p))

                # 🔥 使用LUT快速查找颜色
                color = self._get_color_from_lut(p, self._color_lut)

                if do_shimmer and p > 0.5:
                    shimmer = KARAOKE_SHIMMER * max(0.0, math.sin(now * 6.28 + i * 0.6))
                    shimmer_color = self._get_color_from_lut(shimmer, self._shimmer_lut)
                    color = self._lerp_color_hex(color, shimmer_color, shimmer)

                updates.append((mid, color))

            # 🔥 一次性更新
            for mid, color in updates:
                self.lyric_canvas.itemconfig(mid, fill=color)

        dt = time.perf_counter() - self._last_sync_mono
        if dt > TIME_FREEZE_ON_STALE_SEC:
            next_delay = self._frame_delay_ms(PAUSED_FPS)
        else:
            moving = self._any_char_animating(now)
            # 🔥 渐进式FPS
            if moving:
                target_fps = MAX_FPS_MOVING
            else:
                target_fps = 30 if self.visualizer_enabled else IDLE_FPS
            next_delay = self._frame_delay_ms(target_fps)

        self.root.after(next_delay, self.animation_tick)

    # 🔥 优化：队列处理 - 批量+去重
    def safe_update(self, msg_type, data=None):
        self.message_queue.put((msg_type, data))

    def process_queue(self):
        """批量处理队列消息"""
        processed = 0
        max_processed = 20  # 增加批处理大小

        # 收集同类型消息，仅处理最新的
        pending_updates = {}

        try:
            while processed < max_processed and not self.message_queue.empty():
                msg_type, data = self.message_queue.get_nowait()

                # 时间同步消息：仅保留最新
                if msg_type == "time":
                    pending_updates["time"] = data
                else:
                    pending_updates[msg_type] = data

                processed += 1

            # 批量应用更新
            for msg_type, data in pending_updates.items():
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
                    self._layout_dirty = True
                    self._items_dirty = True
                    self._last_lyric_hash = None
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
                    self._last_lyric_hash = None

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
