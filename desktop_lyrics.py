import asyncio
import websockets
import tkinter as tk
import json
import queue
import threading
import sys
import re
import pystray
from PIL import Image, ImageDraw
from datetime import timedelta
import tkinter.font as tkfont

# 常量定义
BG_COLOR = "black"
HOVER_BG_COLOR = "#606060"
SONG_FG = "#FFD700"
LYRIC_FG = "#E6E6FA"
TRANSLATION_FG = "#98FB98"
FONT_NAME = "Microsoft YaHei UI"
LYRIC_FONT_SIZE = 28
TRANSLATION_FONT_SIZE = 18
SONG_FONT_SIZE = 14
WINDOW_HEIGHT = 180
WINDOW_ALPHA = 0.85
HOVER_ALPHA = 0.75
WEBSOCKET_PORT = 8765

class DesktopLyrics:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Harmonia桌面歌词")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-transparentcolor", BG_COLOR)
        
        # 设置窗口大小和位置
        screen_width = self.root.winfo_screenwidth()
        self.root.geometry(f"{screen_width}x{WINDOW_HEIGHT}+0+100")
        self.root.config(bg=BG_COLOR)
        
        # 首先创建字体对象
        self.setup_fonts()
        
        # 当前状态
        self.connection_status = "disconnected"
        self.current_lyric = ""
        self.current_translation = ""
        self.current_song = ""
        self.current_artist = ""
        self.lyrics_data = []
        self.translations_data = []
        self.last_lyric_index = -1
        self.has_lyrics = False
        
        # 锁定状态
        self.is_locked = False
        
        # 创建UI组件
        self.create_ui()
        
        # 消息队列
        self.message_queue = queue.Queue()
        self.root.after(100, self.process_queue)
        
        # 系统托盘图标
        self.tray_icon = None
        self.create_tray_icon()
        
        # 连接管理器
        self.connected_clients = set()
        
        # 窗口拖动相关变量
        self.drag_data = {"x": 0, "y": 0, "dragging": False}
    
    def setup_fonts(self):
        """设置字体对象"""
        self.lyric_font = tkfont.Font(
            family=FONT_NAME, 
            size=LYRIC_FONT_SIZE, 
            weight="bold"
        )
        self.translation_font = tkfont.Font(
            family=FONT_NAME, 
            size=TRANSLATION_FONT_SIZE,
            weight="normal"
        )
        self.song_font = tkfont.Font(
            family=FONT_NAME,
            size=SONG_FONT_SIZE,
            weight="bold"
        )
    
    def create_ui(self):
        """创建UI组件"""
        # 主框架
        self.main_frame = tk.Frame(self.root, bg=BG_COLOR)
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=10)
        
        # 歌曲信息标签
        self.song_label = tk.Label(
            self.main_frame, 
            text="等待连接...",
            font=self.song_font,
            fg=SONG_FG,
            bg=BG_COLOR,
            pady=8
        )
        self.song_label.pack(anchor="center")
        
        # 歌词标签
        self.lyric_label = tk.Label(
            self.main_frame, 
            text="", 
            font=self.lyric_font,
            fg=LYRIC_FG,
            bg=BG_COLOR, 
            pady=12,
            wraplength=self.root.winfo_screenwidth() - 80,
            justify="center"
        )
        self.lyric_label.pack(expand=True, fill="both")
        
        # 翻译歌词标签
        self.translation_label = tk.Label(
            self.main_frame, 
            text="", 
            font=self.translation_font,
            fg=TRANSLATION_FG,
            bg=BG_COLOR, 
            pady=8
        )
        self.translation_label.pack()
        
        # 绑定鼠标事件
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<B1-Motion>", self.on_move)
        
        # 绑定鼠标悬停事件
        self.root.bind("<Enter>", self.on_enter)
        self.root.bind("<Leave>", self.on_leave)
        
        # 设置窗口透明度
        self.root.attributes("-alpha", WINDOW_ALPHA)
    
    def on_enter(self, event):
        """鼠标进入窗口时调用"""
        if not self.is_locked:
            self.change_background(HOVER_BG_COLOR)
            self.root.attributes("-alpha", HOVER_ALPHA)
    
    def on_leave(self, event):
        """鼠标离开窗口时调用"""
        if not self.is_locked:
            self.change_background(BG_COLOR)
            self.root.attributes("-alpha", WINDOW_ALPHA)
    
    def change_background(self, color):
        """改变所有UI元素的背景色"""
        self.main_frame.config(bg=color)
        self.song_label.config(bg=color)
        self.lyric_label.config(bg=color)
        self.translation_label.config(bg=color)
    
    def toggle_lock(self):
        """切换锁定状态"""
        self.is_locked = not self.is_locked
        
        # 如果锁定，确保背景恢复为黑色
        if self.is_locked:
            self.change_background(BG_COLOR)
            self.root.attributes("-alpha", WINDOW_ALPHA)
        
        # 更新托盘菜单
        self.update_tray_menu()
        print(f"锁定状态: {'已锁定' if self.is_locked else '未锁定'}")
    
    def update_tray_menu(self):
        """更新托盘菜单"""
        if self.tray_icon:
            self.tray_icon.update_menu()
    
    def create_tray_icon(self):
        """创建系统托盘图标"""
        try:
            # 创建音符图标
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # 绘制音符
            draw.ellipse([(20, 12), (44, 36)], fill="#E6E6FA", outline="#FFFFFF", width=2)
            draw.rectangle([(42, 18), (46, 50)], fill="#E6E6FA")
            points = [(38, 28), (52, 22), (52, 34), (38, 28)]
            draw.polygon(points, fill="#E6E6FA")
            
            # 创建托盘菜单 - 使用动态菜单
            self.tray_menu = pystray.Menu(
                pystray.MenuItem(
                    lambda item: "解锁" if self.is_locked else "锁定", 
                    self.toggle_lock
                ),
                pystray.MenuItem("断开连接", self.disconnect_client),
                pystray.MenuItem("退出", self.quit_application)
            )
            
            # 创建托盘图标
            self.tray_icon = pystray.Icon(
                "harmonia_lyrics", 
                image, 
                "Harmonia桌面歌词", 
                self.tray_menu
            )
            
            # 在单独的线程中运行托盘图标
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as e:
            print(f"创建托盘图标失败: {e}")
    
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
    
    def start_move(self, event):
        """开始拖动窗口"""
        if not self.is_locked:  # 只有在未锁定时才能拖动
            self.drag_data["x"] = event.x
            self.drag_data["y"] = event.y
            self.drag_data["dragging"] = True
    
    def stop_move(self, event):
        """停止拖动窗口"""
        self.drag_data["dragging"] = False
    
    def on_move(self, event):
        """处理窗口拖动"""
        if not self.drag_data["dragging"] or self.is_locked:
            return
            
        deltax = event.x - self.drag_data["x"]
        deltay = event.y - self.drag_data["y"]
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        
        # 确保窗口不会移出屏幕
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        window_width = self.root.winfo_width()
        
        x = max(0, min(x, screen_width - window_width))
        y = max(0, min(y, screen_height - WINDOW_HEIGHT))
        
        self.root.geometry(f"+{x}+{y}")
    
    def update_status(self, status):
        """更新连接状态"""
        self.connection_status = status
        if status == "connected":
            self.song_label.config(text="已连接 - 等待歌曲...", fg=SONG_FG)
        elif status == "disconnected":
            self.song_label.config(text="等待连接...", fg=SONG_FG)
    
    def parse_lyrics(self, lyric_text):
        """解析LRC格式歌词"""
        if not lyric_text:
            return []
        
        lyrics = []
        time_regex = r'\[(\d+):(\d+)(?:\.|:)(\d+)\]'
        
        for line in lyric_text.split('\n'):
            if not line.strip() or line.startswith('[') and not re.search(time_regex, line):
                continue
                
            matches = re.findall(time_regex, line)
            if not matches:
                continue
                
            text = re.sub(time_regex, '', line).strip()
            if not text:
                continue
                
            for match in matches:
                try:
                    minutes = int(match[0])
                    seconds = int(match[1])
                    milliseconds = int(match[2])
                    
                    if milliseconds < 100:
                        milliseconds *= 10
                    
                    total_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                    lyrics.append({'time': total_seconds, 'text': text})
                except ValueError:
                    continue
        
        lyrics.sort(key=lambda x: x['time'])
        return lyrics
    
    def update_lyrics_with_time(self, current_time):
        """根据当前时间更新歌词显示"""
        if not self.lyrics_data:
            return
            
        current_index = -1
        for i, lyric in enumerate(self.lyrics_data):
            if lyric['time'] <= current_time:
                current_index = i
            else:
                break
        
        if current_index != -1 and current_index != self.last_lyric_index:
            self.last_lyric_index = current_index
            self.current_lyric = self.lyrics_data[current_index]['text']
            
            self.lyric_label.config(text=self.current_lyric)
            
            self.current_translation = ""
            if self.translations_data:
                translation_index = -1
                for i, translation in enumerate(self.translations_data):
                    if translation['time'] <= current_time:
                        translation_index = i
                    else:
                        break
                
                if translation_index != -1:
                    lyric_time = self.lyrics_data[current_index]['time']
                    translation_time = self.translations_data[translation_index]['time']
                    
                    if abs(lyric_time - translation_time) < 0.5:
                        self.current_translation = self.translations_data[translation_index]['text']
            
            self.translation_label.config(text=self.current_translation)
    
    def update_full_lyrics(self, lyric, tlyric):
        """更新完整歌词数据"""
        try:
            self.lyrics_data = self.parse_lyrics(lyric) if lyric else []
            self.translations_data = self.parse_lyrics(tlyric) if tlyric else []
            self.last_lyric_index = -1
            
            self.has_lyrics = bool(self.lyrics_data)
            
            print(f"收到歌词: {len(self.lyrics_data)}行, 翻译: {len(self.translations_data)}行")
            
            if not self.has_lyrics:
                self.lyric_label.config(text="当前歌曲无歌词/正在等待网页传输")
        except Exception as e:
            print(f"解析歌词时出错: {e}")
            self.lyric_label.config(text="歌词解析错误")
    
    def safe_update(self, msg_type, data=None):
        """线程安全的方式更新UI"""
        self.message_queue.put((msg_type, data))
    
    def process_queue(self):
        """处理消息队列中的消息"""
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
                    self.lyric_label.config(text="正在加载歌词...")
                    self.translation_label.config(text="")
                elif msg_type == "full_lyric":
                    self.update_full_lyrics(data.get('lyric', ''), data.get('tlyric', ''))
                elif msg_type == "time":
                    self.update_lyrics_with_time(data)
                elif msg_type == "clear":
                    self.lyric_label.config(text="")
                    self.translation_label.config(text="")
                    self.song_label.config(text="等待连接...", fg=SONG_FG)
                    self.current_lyric = ""
                    self.current_translation = ""
                    self.current_song = ""
                    self.current_artist = ""
                    self.lyrics_data = []
                    self.translations_data = []
                    self.last_lyric_index = -1
                    self.has_lyrics = False
                
                processed += 1
                
        except Exception as e:
            print(f"处理队列时出错: {e}")
        
        self.root.after(100, self.process_queue)
    
    def run(self):
        """运行主循环"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            self.quit_application()
        except Exception as e:
            print(f"主循环错误: {e}")
            self.quit_application()

def start_websocket_server(desktop_lyrics):
    """启动WebSocket服务器"""
    async def handle_connection(websocket):
        """处理WebSocket连接"""
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
        """WebSocket服务器主循环"""
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
    
    server_thread = threading.Thread(
        target=start_websocket_server, 
        args=(desktop_lyrics,),
        daemon=True
    )
    server_thread.start()
    
    desktop_lyrics.run()
    
    print("程序已退出")
