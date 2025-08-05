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
SONG_FG = "yellow"
LYRIC_FG = "#9b59b6"
TRANSLATION_FG = "yellow"
FONT_NAME = "Microsoft YaHei"
LYRIC_FONT_SIZE = 24
TRANSLATION_FONT_SIZE = 16
SONG_FONT_SIZE = 12
WINDOW_HEIGHT = 150
WINDOW_ALPHA = 0.9
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
        
        # 创建UI组件
        self.create_ui()
        
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
        
        # 创建字体对象
        self.lyric_font = tkfont.Font(family=FONT_NAME, size=LYRIC_FONT_SIZE, weight="bold")
        self.translation_font = tkfont.Font(family=FONT_NAME, size=TRANSLATION_FONT_SIZE)
        
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
    
    def create_ui(self):
        """创建UI组件"""
        # 歌曲信息标签
        self.song_label = tk.Label(
            self.root, 
            text="等待连接...",
            font=(FONT_NAME, SONG_FONT_SIZE),
            fg=SONG_FG,
            bg=BG_COLOR,
            padx=10,
            pady=5
        )
        self.song_label.pack(anchor="center")
        
        # 歌词标签
        self.lyric_label = tk.Label(
            self.root, 
            text="", 
            font=(FONT_NAME, LYRIC_FONT_SIZE, "bold"),
            fg=LYRIC_FG,
            bg=BG_COLOR, 
            padx=20,
            pady=10,
            wraplength=self.root.winfo_screenwidth() - 40  # 自动换行
        )
        self.lyric_label.pack(expand=True, fill="both")
        
        # 翻译歌词标签
        self.translation_label = tk.Label(
            self.root, 
            text="", 
            font=(FONT_NAME, TRANSLATION_FONT_SIZE),
            fg=TRANSLATION_FG,
            bg=BG_COLOR, 
            padx=20,
            pady=5
        )
        self.translation_label.pack()
        
        # 绑定鼠标事件
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<B1-Motion>", self.on_move)
        
        # 设置窗口透明度
        self.root.attributes("-alpha", WINDOW_ALPHA)
    
    def create_tray_icon(self):
        """创建更美观的系统托盘图标"""
        try:
            # 创建简单的音符图标
            image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            
            # 绘制音符主体
            draw.ellipse([(15, 10), (50, 45)], fill="yellow")
            
            # 绘制音符杆
            draw.rectangle([(40, 15), (45, 55)], fill="yellow")
            
            # 绘制音符尾
            points = [(35, 30), (55, 20), (55, 40), (35, 30)]
            draw.polygon(points, fill="yellow")
            
            # 创建托盘菜单
            menu = pystray.Menu(
                pystray.MenuItem("断开连接", self.disconnect_client),
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
        self.drag_data["x"] = event.x
        self.drag_data["y"] = event.y
        self.drag_data["dragging"] = True
    
    def stop_move(self, event):
        """停止拖动窗口"""
        self.drag_data["dragging"] = False
    
    def on_move(self, event):
        """处理窗口拖动"""
        if not self.drag_data["dragging"]:
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
        """解析LRC格式歌词 - 更健壮的解析器"""
        if not lyric_text:
            return []
        
        lyrics = []
        time_regex = r'\[(\d+):(\d+)(?:\.|:)(\d+)\]'
        
        for line in lyric_text.split('\n'):
            # 跳过空行和元数据行
            if not line.strip() or line.startswith('[') and not re.search(time_regex, line):
                continue
                
            # 提取所有时间标签
            matches = re.findall(time_regex, line)
            if not matches:
                continue
                
            # 获取歌词文本
            text = re.sub(time_regex, '', line).strip()
            if not text:
                continue
                
            for match in matches:
                try:
                    minutes = int(match[0])
                    seconds = int(match[1])
                    milliseconds = int(match[2])
                    
                    # 处理毫秒精度
                    if milliseconds < 100:
                        milliseconds *= 10
                    
                    total_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                    lyrics.append({'time': total_seconds, 'text': text})
                except ValueError:
                    continue
        
        # 按时间排序
        lyrics.sort(key=lambda x: x['time'])
        return lyrics
    
    def update_lyrics_with_time(self, current_time):
        """根据当前时间更新歌词显示 - 添加平滑滚动效果"""
        if not self.lyrics_data:
            return
            
        # 找到当前应该显示的歌词
        current_index = -1
        for i, lyric in enumerate(self.lyrics_data):
            if lyric['time'] <= current_time:
                current_index = i
            else:
                break
        
        # 如果找到了新的歌词行
        if current_index != -1 and current_index != self.last_lyric_index:
            self.last_lyric_index = current_index
            self.current_lyric = self.lyrics_data[current_index]['text']
            
            # 更新歌词标签
            self.lyric_label.config(text=self.current_lyric)
            
            # 查找对应的翻译
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
        """更新完整歌词数据 - 添加错误处理"""
        try:
            self.lyrics_data = self.parse_lyrics(lyric) if lyric else []
            self.translations_data = self.parse_lyrics(tlyric) if tlyric else []
            self.last_lyric_index = -1
            
            # 检查是否有歌词
            self.has_lyrics = bool(self.lyrics_data)
            
            print(f"收到歌词: {len(self.lyrics_data)}行, 翻译: {len(self.translations_data)}行")
            
            # 如果没有歌词，显示提示
            if not self.has_lyrics:
                self.lyric_label.config(text="当前歌曲无歌词/正在等待网页传输")
        except Exception as e:
            print(f"解析歌词时出错: {e}")
            self.lyric_label.config(text="歌词解析错误")
    
    def safe_update(self, msg_type, data=None):
        """线程安全的方式更新UI - 添加批量处理"""
        self.message_queue.put((msg_type, data))
    
    def process_queue(self):
        """处理消息队列中的消息 - 优化性能"""
        processed = 0
        max_processed = 10  # 一次处理的最大消息数
        
        try:
            while processed < max_processed and not self.message_queue.empty():
                msg_type, data = self.message_queue.get_nowait()
                
                if msg_type == "status":
                    self.update_status(data)
                elif msg_type == "song":
                    self.current_song = data.get('song', '')
                    self.current_artist = data.get('artist', '')
                    song_text = f"{self.current_song} - {self.current_artist}"[:80]  # 限制长度
                    self.song_label.config(text=song_text, fg=SONG_FG)
                    
                    # 重置歌词状态
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
        
        # 继续定期检查队列
        self.root.after(100, self.process_queue)
    
    def run(self):
        """运行主循环 - 添加异常处理"""
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            self.quit_application()
        except Exception as e:
            print(f"主循环错误: {e}")
            self.quit_application()

def start_websocket_server(desktop_lyrics):
    """启动WebSocket服务器 - 重构为更清晰的函数"""
    async def handle_connection(websocket):
        """处理WebSocket连接 - 添加心跳检测"""
        print("客户端已连接")
        desktop_lyrics.connected_clients.add(websocket)
        desktop_lyrics.safe_update("status", "connected")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    
                    # 心跳检测处理
                    if data.get('type') == 'ping':
                        await websocket.send(json.dumps({'type': 'pong'}))
                        continue
                    
                    # 处理其他消息类型
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
            await asyncio.Future()  # 永久运行
    
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(websocket_server())
    except Exception as e:
        print(f"WebSocket服务器错误: {e}")
    finally:
        loop.close()

if __name__ == "__main__":
    # 创建桌面歌词实例
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
