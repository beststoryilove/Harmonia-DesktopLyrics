import asyncio
import websockets
import tkinter as tk
import json
import queue
import threading
import sys
import re
import pystray
import time
from PIL import Image
from datetime import timedelta
from tkinter import font as tkfont

class DesktopLyrics:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Harmonia桌面歌词")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", "black")
        
        # 获取屏幕尺寸并设置窗口位置
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"{screen_width}x150+0+{screen_height//4}")
        
        # 设置颜色常量
        self.COLORS = {
            "background": "black",
            "song_info": "yellow",
            "played_lyric": "#FFD700",  # 金色
            "unplayed_lyric": "#9b59b6",  # 紫色
            "played_translation": "#FFFF00",  # 亮黄色
            "unplayed_translation": "#CCCC00"  # 暗黄色
        }
        
        self.root.config(bg=self.COLORS["background"])
        
        # 创建字体对象以便复用
        self.song_font = ("Microsoft YaHei", 12)
        self.lyric_font = ("Microsoft YaHei", 24, "bold")
        self.translation_font = ("Microsoft YaHei", 16)
        
        # 歌曲信息标签
        self.song_label = tk.Label(
            self.root, 
            text="等待连接...",
            font=self.song_font,
            fg=self.COLORS["song_info"],
            bg=self.COLORS["background"],
            padx=10,
            pady=5
        )
        self.song_label.pack(anchor="center")
        
        # 主容器
        self.center_container = tk.Frame(self.root, bg=self.COLORS["background"])
        self.center_container.pack(expand=True, fill="both")
        
        # 歌词容器
        self.lyrics_frame = tk.Frame(self.center_container, bg=self.COLORS["background"])
        self.lyrics_frame.pack(expand=True, pady=5)
        
        # 歌词标签容器
        self.lyrics_container = tk.Frame(self.lyrics_frame, bg=self.COLORS["background"])
        self.lyrics_container.pack()
        
        # 歌词标签
        self.played_lyric = tk.Label(
            self.lyrics_container, 
            text="", 
            font=self.lyric_font,
            fg=self.COLORS["played_lyric"],
            bg=self.COLORS["background"], 
            padx=0
        )
        self.played_lyric.pack(side="left")
        
        self.unplayed_lyric = tk.Label(
            self.lyrics_container, 
            text="", 
            font=self.lyric_font,
            fg=self.COLORS["unplayed_lyric"],
            bg=self.COLORS["background"], 
            padx=0
        )
        self.unplayed_lyric.pack(side="left")
        
        # 翻译容器
        self.translation_frame = tk.Frame(self.center_container, bg=self.COLORS["background"])
        self.translation_frame.pack(pady=5)
        
        # 翻译标签容器
        self.translation_container = tk.Frame(self.translation_frame, bg=self.COLORS["background"])
        self.translation_container.pack()
        
        # 翻译标签
        self.played_translation = tk.Label(
            self.translation_container, 
            text="", 
            font=self.translation_font,
            fg=self.COLORS["played_translation"],
            bg=self.COLORS["background"], 
            padx=0,
            pady=5
        )
        self.played_translation.pack(side="left")
        
        self.unplayed_translation = tk.Label(
            self.translation_container, 
            text="", 
            font=self.translation_font,
            fg=self.COLORS["unplayed_translation"],
            bg=self.COLORS["background"], 
            padx=0,
            pady=5
        )
        self.unplayed_translation.pack(side="left")
        
        # 绑定鼠标事件
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<B1-Motion>", self.on_move)
        self.root.bind("<Double-Button-1>", self.toggle_transparency)  # 双击切换透明度
        self.root.bind("<MouseWheel>", self.adjust_font_size)  # 滚轮调整字体大小
        
        # 设置窗口半透明
        self.transparency_level = 0.9
        self.root.attributes("-alpha", self.transparency_level)
        
        # 当前状态
        self.connection_status = "disconnected"
        self.current_lyric = ""
        self.current_translation = ""
        self.current_song = ""
        self.current_artist = ""
        self.lyrics_data = []
        self.translations_data = []
        self.last_lyric_index = -1
        self.current_line_duration = 0
        self.line_start_time = 0
        self.last_progress = 0
        
        # 消息队列
        self.message_queue = queue.Queue()
        
        # 定期检查消息队列（提高频率）
        self.root.after(50, self.process_queue)
        
        # 系统托盘图标
        self.tray_icon = None
        self.create_tray_icon()
        
        # 连接管理器
        self.connected_clients = set()
        
        # 字体大小调整
        self.font_size_multiplier = 1.0
        self.min_font_size = 0.7
        self.max_font_size = 1.5
    
    def toggle_transparency(self, event):
        """双击切换窗口透明度"""
        self.transparency_level = 0.5 if self.transparency_level > 0.7 else 0.9
        self.root.attributes("-alpha", self.transparency_level)
    
    def adjust_font_size(self, event):
        """滚轮调整字体大小"""
        delta = event.delta
        if delta > 0:  # 滚轮向上
            self.font_size_multiplier = min(self.max_font_size, self.font_size_multiplier + 0.05)
        else:  # 滚轮向下
            self.font_size_multiplier = max(self.min_font_size, self.font_size_multiplier - 0.05)
        
        # 更新字体
        self.update_fonts()
    
    def update_fonts(self):
        """根据当前缩放比例更新字体"""
        # 歌曲信息字体
        base_size = int(12 * self.font_size_multiplier)
        self.song_font = ("Microsoft YaHei", base_size)
        self.song_label.config(font=self.song_font)
        
        # 歌词字体
        lyric_size = int(24 * self.font_size_multiplier)
        self.lyric_font = ("Microsoft YaHei", lyric_size, "bold")
        self.played_lyric.config(font=self.lyric_font)
        self.unplayed_lyric.config(font=self.lyric_font)
        
        # 翻译字体
        trans_size = int(16 * self.font_size_multiplier)
        self.translation_font = ("Microsoft YaHei", trans_size)
        self.played_translation.config(font=self.translation_font)
        self.unplayed_translation.config(font=self.translation_font)
    
    def create_tray_icon(self):
        """创建系统托盘图标"""
        # 创建图标图像
        image = Image.new('RGB', (64, 64), "black")
        # 绘制一个简单的音符图标
        for i in range(20, 44):
            for j in range(10, 30):
                if 20 <= i <= 40 or (j == 20 and 25 <= i <= 35):
                    image.putpixel((i, j), (255, 255, 0))
        
        # 创建托盘菜单
        menu = pystray.Menu(
            pystray.MenuItem("断开连接", self.disconnect_client),
            pystray.MenuItem("调整位置", self.reset_position),
            pystray.MenuItem("退出", self.quit_application)
        )
        
        # 创建托盘图标
        self.tray_icon = pystray.Icon(
            "harmonia_lyrics", 
            image, 
            "Harmonia桌面歌词", 
            menu
        )
        
        # 在单独的线程中运行托盘图标
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
    
    def reset_position(self):
        """重置窗口位置到屏幕顶部中央"""
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        self.root.geometry(f"+{screen_width//4}+{screen_height//8}")
    
    def disconnect_client(self):
        """断开当前连接的客户端"""
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
    
    def quit_application(self):
        """退出应用程序"""
        print("退出应用程序")
        self.disconnect_client()
        self.root.after(100, self.root.destroy)
        if self.tray_icon:
            self.tray_icon.stop()
        sys.exit(0)
    
    def start_move(self, event):
        self.x = event.x
        self.y = event.y
    
    def stop_move(self, event):
        self.x = None
        self.y = None
    
    def on_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")
    
    def update_status(self, status):
        """更新连接状态"""
        self.connection_status = status
        if status == "connected":
            self.song_label.config(text="已连接 - 等待歌曲...", fg=self.COLORS["song_info"])
        elif status == "disconnected":
            self.song_label.config(text="等待连接...", fg=self.COLORS["song_info"])
    
    def parse_lyrics(self, lyric_text):
        """解析LRC格式歌词"""
        if not lyric_text:
            return []
            
        lines = lyric_text.split('\n')
        lyrics = []
        
        # 预编译正则表达式提高性能
        time_regex = re.compile(r'\[(\d+):(\d+)(?:\.|:)(\d+)\]')
        
        for line in lines:
            matches = time_regex.findall(line)
            if matches:
                text = time_regex.sub('', line).strip()
                if text:
                    for match in matches:
                        try:
                            minutes = int(match[0])
                            seconds = int(match[1])
                            milliseconds = int(match[2])
                            
                            if milliseconds < 100:
                                milliseconds *= 10
                            
                            total_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                            lyrics.append({
                                'time': total_seconds,
                                'text': text
                            })
                        except (ValueError, TypeError):
                            continue  # 忽略格式错误的时间标签
        
        # 按时间排序
        lyrics.sort(key=lambda x: x['time'])
        return lyrics
    
    def update_lyrics_with_time(self, current_time):
        """根据当前时间更新歌词显示（带逐字变色效果）"""
        if not self.lyrics_data:
            self.clear_lyrics_display()
            return
        
        # 找到当前应该显示的歌词
        current_index = -1
        for i, lyric in enumerate(self.lyrics_data):
            if lyric['time'] <= current_time:
                current_index = i
            else:
                break
        
        if current_index == -1:
            self.clear_lyrics_display()
            return
        
        # 如果找到了新的歌词行
        if current_index != self.last_lyric_index:
            self.last_lyric_index = current_index
            self.current_lyric = self.lyrics_data[current_index]['text']
            self.line_start_time = self.lyrics_data[current_index]['time']
            
            # 计算当前行的持续时间
            if current_index < len(self.lyrics_data) - 1:
                self.current_line_duration = self.lyrics_data[current_index + 1]['time'] - self.line_start_time
            else:
                self.current_line_duration = 5.0
            
            # 重置歌词显示
            self.played_lyric.config(text="")
            self.unplayed_lyric.config(text=self.current_lyric)
            self.last_progress = 0
        
        # 计算当前行已播放的时间
        elapsed = current_time - self.line_start_time
        
        # 计算变色位置
        if self.current_line_duration > 0 and elapsed > 0:
            progress = min(1.0, elapsed / self.current_line_duration)
            
            # 优化平滑过渡逻辑
            # 如果进度增加超过0.1，直接跳转到当前进度
            if progress - self.last_progress > 0.1:
                self.last_progress = progress
            else:
                # 否则平滑过渡，但加速过渡速度
                self.last_progress = min(progress, self.last_progress + 0.08)
            
            chars_to_color = int(len(self.current_lyric) * self.last_progress)
            
            # 分割歌词
            played_part = self.current_lyric[:chars_to_color]
            unplayed_part = self.current_lyric[chars_to_color:]
            
            # 更新显示
            self.played_lyric.config(text=played_part)
            self.unplayed_lyric.config(text=unplayed_part)
        
        # 处理翻译
        self.update_translation(current_time, current_index)
    
    def update_translation(self, current_time, lyric_index):
        """更新翻译显示"""
        if not self.translations_data:
            self.played_translation.config(text="")
            self.unplayed_translation.config(text="")
            return
        
        # 找到最接近当前时间但不超过当前时间的翻译
        translation_index = -1
        for i, translation in enumerate(self.translations_data):
            if translation['time'] <= current_time:
                translation_index = i
            else:
                break
        
        if translation_index == -1:
            self.played_translation.config(text="")
            self.unplayed_translation.config(text="")
            return
        
        # 检查翻译时间是否与歌词时间匹配（允许0.5秒误差）
        lyric_time = self.lyrics_data[lyric_index]['time']
        translation_time = self.translations_data[translation_index]['time']
        
        if abs(lyric_time - translation_time) > 0.5:
            self.played_translation.config(text="")
            self.unplayed_translation.config(text="")
            return
        
        self.current_translation = self.translations_data[translation_index]['text']
        
        # 计算翻译变色位置（使用相同的进度）
        if self.current_line_duration > 0:
            elapsed = current_time - self.line_start_time
            progress = min(1.0, elapsed / self.current_line_duration)
            chars_to_color = int(len(self.current_translation) * progress)
            
            # 分割翻译
            played_trans = self.current_translation[:chars_to_color]
            unplayed_trans = self.current_translation[chars_to_color:]
            
            # 更新显示
            self.played_translation.config(text=played_trans)
            self.unplayed_translation.config(text=unplayed_trans)
        else:
            self.played_translation.config(text="")
            self.unplayed_translation.config(text=self.current_translation)
    
    def clear_lyrics_display(self):
        """清空歌词显示"""
        self.played_lyric.config(text="")
        self.unplayed_lyric.config(text="")
        self.played_translation.config(text="")
        self.unplayed_translation.config(text="")
    
    def update_full_lyrics(self, lyric, tlyric):
        """更新完整歌词数据"""
        self.lyrics_data = self.parse_lyrics(lyric) if lyric else []
        self.translations_data = self.parse_lyrics(tlyric) if tlyric else []
        self.last_lyric_index = -1
        self.last_progress = 0
        print(f"收到完整歌词: {len(self.lyrics_data)}行, 翻译: {len(self.translations_data)}行")
    
    def safe_update(self, msg_type, data=None):
        """线程安全的方式更新UI"""
        self.message_queue.put((msg_type, data))
    
    def process_queue(self):
        """处理消息队列中的消息"""
        try:
            while not self.message_queue.empty():
                msg_type, data = self.message_queue.get_nowait()
                
                if msg_type == "status":
                    self.update_status(data)
                elif msg_type == "song":
                    self.current_song = data.get('song', '')
                    self.current_artist = data.get('artist', '')
                    song_text = f"{self.current_song} - {self.current_artist}"
                    self.song_label.config(text=song_text, fg=self.COLORS["song_info"])
                elif msg_type == "full_lyric":
                    self.update_full_lyrics(data.get('lyric', ''), data.get('tlyric', ''))
                elif msg_type == "time":
                    # 立即处理时间更新
                    self.update_lyrics_with_time(data)
                elif msg_type == "clear":
                    self.clear_lyrics_display()
                    self.song_label.config(text="等待连接...", fg=self.COLORS["song_info"])
                    self.current_lyric = ""
                    self.current_translation = ""
                    self.current_song = ""
                    self.current_artist = ""
                    self.lyrics_data = []
                    self.translations_data = []
                    self.last_lyric_index = -1
                    self.last_progress = 0
                    
        except queue.Empty:
            pass
        except Exception as e:
            print(f"处理队列时出错: {e}")
        
        # 继续定期检查队列（高频）
        self.root.after(50, self.process_queue)
    
    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            self.quit_application()
        except Exception as e:
            print(f"主循环错误: {e}")
            self.quit_application()

def start_websocket_server(desktop_lyrics):
    async def handle_connection(websocket):
        """处理WebSocket连接"""
        print("客户端已连接")
        desktop_lyrics.connected_clients.add(websocket)
        desktop_lyrics.safe_update("status", "connected")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
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
                        current_time = data.get('currentTime', 0)
                        if current_time >= 0:  # 验证时间有效性
                            desktop_lyrics.safe_update("time", current_time)
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
        async with websockets.serve(handle_connection, "localhost", 8765):
            print("WebSocket服务器已启动，监听端口 8765")
            await asyncio.Future()  # 永久运行
    
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(websocket_server())
    except KeyboardInterrupt:
        print("WebSocket服务器已停止")
    finally:
        loop.close()

if __name__ == "__main__":
    desktop_lyrics = DesktopLyrics()
    
    # 在单独的线程中启动WebSocket服务器
    server_thread = threading.Thread(
        target=start_websocket_server, 
        args=(desktop_lyrics,),
        daemon=True
    )
    server_thread.start()
    
    # 在主线程中启动Tkinter主循环
    desktop_lyrics.run()
    
    print("程序已退出")
