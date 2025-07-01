import asyncio
import websockets
import tkinter as tk
import json
import queue
import threading
import sys
import re
from datetime import timedelta

class DesktopLyrics:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Harmonia桌面歌词")
        self.root.overrideredirect(True)  # 无边框
        self.root.attributes("-topmost", True)  # 置顶
        self.root.attributes("-transparentcolor", "black")  # 透明背景
        
        # 设置窗口大小为屏幕宽度 x 150，位置在屏幕顶部
        screen_width = self.root.winfo_screenwidth()
        self.root.geometry(f"{screen_width}x150+0+100")
        
        # 设置窗口背景为黑色（透明部分）
        self.root.config(bg="black")
        
        # 创建歌曲信息标签（放在顶部）
        self.song_label = tk.Label(
            self.root, 
            text="等待连接...",  # 初始状态
            font=("Microsoft YaHei", 12),
            fg="#aaaaaa", 
            bg="black",
            padx=10,
            pady=5
        )
        self.song_label.pack(anchor="center")
        
        # 创建歌词标签（放在中间）
        self.lyric_label = tk.Label(
            self.root, 
            text="", 
            font=("Microsoft YaHei", 24, "bold"),
            fg="#9b59b6", 
            bg="black", 
            padx=20,
            pady=10
        )
        self.lyric_label.pack(expand=True, fill="both")
        
        # 创建翻译歌词标签（放在底部）
        self.translation_label = tk.Label(
            self.root, 
            text="", 
            font=("Microsoft YaHei", 16),
            fg="#777777", 
            bg="black", 
            padx=20,
            pady=5
        )
        self.translation_label.pack()
        
        # 绑定鼠标事件实现窗口拖动
        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<ButtonRelease-1>", self.stop_move)
        self.root.bind("<B1-Motion>", self.on_move)
        
        # 设置窗口半透明
        self.root.attributes("-alpha", 0.9)
        
        # 当前状态
        self.connection_status = "disconnected"  # disconnected/connected
        self.current_lyric = ""
        self.current_translation = ""
        self.current_song = ""
        self.current_artist = ""
        self.lyrics_data = []  # 存储解析后的歌词数据
        self.translations_data = []  # 存储解析后的翻译数据
        self.last_lyric_index = -1
        
        # 消息队列
        self.message_queue = queue.Queue()
        
        # 定期检查消息队列
        self.root.after(100, self.process_queue)
    
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
            self.song_label.config(text="已连接 - 等待歌曲...", fg="#2ecc71")
        elif status == "disconnected":
            self.song_label.config(text="等待连接...", fg="#aaaaaa")
    
    def parse_lyrics(self, lyric_text):
        """解析LRC格式歌词"""
        lines = lyric_text.split('\n')
        lyrics = []
        
        # 正则表达式匹配时间标签 [mm:ss.xx] 或 [mm:ss:xx]
        time_regex = r'\[(\d+):(\d+)(?:\.|:)(\d+)\]'
        
        for line in lines:
            matches = re.findall(time_regex, line)
            if matches:
                # 获取歌词文本（移除所有时间标签）
                text = re.sub(time_regex, '', line).strip()
                if text:
                    for match in matches:
                        minutes = int(match[0])
                        seconds = int(match[1])
                        milliseconds = int(match[2])
                        
                        # 处理不同精度的毫秒数
                        if milliseconds < 100:  # 两位数的毫秒
                            milliseconds *= 10
                        
                        total_seconds = minutes * 60 + seconds + milliseconds / 1000.0
                        lyrics.append({
                            'time': total_seconds,
                            'text': text
                        })
        
        # 按时间排序
        lyrics.sort(key=lambda x: x['time'])
        return lyrics
    
    def update_lyrics_with_time(self, current_time):
        """根据当前时间更新歌词显示"""
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
            self.lyric_label.config(text=self.current_lyric)
            
            # 如果有翻译，查找对应时间点的翻译
            self.current_translation = ""
            if self.translations_data:
                # 找到最接近当前时间但不超过当前时间的翻译
                translation_index = -1
                for i, translation in enumerate(self.translations_data):
                    if translation['time'] <= current_time:
                        translation_index = i
                    else:
                        break
                
                if translation_index != -1:
                    # 检查翻译时间是否与歌词时间匹配（允许0.5秒误差）
                    lyric_time = self.lyrics_data[current_index]['time']
                    translation_time = self.translations_data[translation_index]['time']
                    
                    if abs(lyric_time - translation_time) < 0.5:
                        self.current_translation = self.translations_data[translation_index]['text']
            
            self.translation_label.config(text=self.current_translation)
    
    def update_full_lyrics(self, lyric, tlyric):
        """更新完整歌词数据"""
        self.lyrics_data = self.parse_lyrics(lyric) if lyric else []
        self.translations_data = self.parse_lyrics(tlyric) if tlyric else []
        self.last_lyric_index = -1
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
                    self.update_status(data)  # data是状态字符串
                elif msg_type == "song":
                    self.current_song = data.get('song', '')
                    self.current_artist = data.get('artist', '')
                    song_text = f"{self.current_song} - {self.current_artist}"
                    self.song_label.config(text=song_text, fg="#aaaaaa")
                elif msg_type == "full_lyric":
                    self.update_full_lyrics(data.get('lyric', ''), data.get('tlyric', ''))
                elif msg_type == "time":
                    self.update_lyrics_with_time(data)
                elif msg_type == "clear":
                    self.lyric_label.config(text="")
                    self.translation_label.config(text="")
                    self.song_label.config(text="等待连接...", fg="#aaaaaa")
                    self.current_lyric = ""
                    self.current_translation = ""
                    self.current_song = ""
                    self.current_artist = ""
                    self.lyrics_data = []
                    self.translations_data = []
                    self.last_lyric_index = -1
                    
        except queue.Empty:
            pass
        except Exception as e:
            print(f"处理队列时出错: {e}")
        
        # 继续定期检查队列
        self.root.after(100, self.process_queue)
    
    def run(self):
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\n程序已退出")
            sys.exit(0)

def start_websocket_server():
    async def handle_connection(websocket):
        """处理WebSocket连接"""
        print("客户端已连接")
        # 更新连接状态
        desktop_lyrics.safe_update("status", "connected")
        
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    print(f"收到消息类型: {data.get('type')}")  # 调试日志
                    
                    if data.get('type') == 'song':
                        desktop_lyrics.safe_update("song", {
                            'song': data.get('song', ''),
                            'artist': data.get('artist', '')
                        })
                    elif data.get('type') == 'full_lyric':
                        desktop_lyrics.safe_update("full_lyric", {
                            'lyric': data.get('lyric', ''),
                            'tlyric': data.get('tlyric', '')
                        })
                    elif data.get('type') == 'time':
                        desktop_lyrics.safe_update("time", data.get('currentTime', 0))
                except Exception as e:
                    print(f"处理消息时出错: {e}")
        except websockets.exceptions.ConnectionClosed:
            print("客户端已断开连接")
            # 更新连接状态
            desktop_lyrics.safe_update("status", "disconnected")
        except Exception as e:
            print(f"连接错误: {e}")
            desktop_lyrics.safe_update("status", "disconnected")
    
    async def websocket_server():
        async with websockets.serve(handle_connection, "localhost", 8765):
            print("WebSocket服务器已启动，监听端口 8765")
            await asyncio.Future()  # 永久运行
    
    # 创建新的事件循环
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(websocket_server())

if __name__ == "__main__":
    # 创建桌面歌词实例
    desktop_lyrics = DesktopLyrics()
    
    # 在单独的线程中启动WebSocket服务器
    server_thread = threading.Thread(target=start_websocket_server, daemon=True)
    server_thread.start()
    
    # 在主线程中启动Tkinter主循环
    desktop_lyrics.run()
    
    print("程序已退出")