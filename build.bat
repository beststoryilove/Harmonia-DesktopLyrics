@echo off
set PYTHONOPTIMIZE=1

echo ��������ɹ���...
rmdir /s /q build
rmdir /s /q dist

echo ���ڰ�װ����...
pip install pyinstaller websockets

echo ���ڹ�����ִ���ļ�...
pyinstaller --onefile --windowed --icon icon.ico --name "Harmonia������" --add-data "icon.ico;." desktop_lyrics.py

echo ������ɣ���ִ���ļ��� dist �ļ�����
pause