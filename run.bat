@echo off
cd /d "%~dp0"
REM 桌面版（tkinter 一体化，无网页控制台）。注意：需带 tkinter 的 Python 3.14（系统版），
REM venv 3.13 无 tkinter 不可用。
start "" "C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" desktop_app.py
