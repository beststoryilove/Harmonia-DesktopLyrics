@echo off
set PYTHONOPTIMIZE=1

echo 正在清理旧构建...
rmdir /s /q build
rmdir /s /q dist

echo 正在安装依赖...
pip install pyinstaller websockets

echo 正在构建可执行文件...
pyinstaller --onefile --windowed --icon icon.ico --name "Harmonia桌面歌词" --add-data "icon.ico;." desktop_lyrics.py

echo 构建完成！可执行文件在 dist 文件夹中
pause