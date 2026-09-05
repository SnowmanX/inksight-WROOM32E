@echo off
REM InkSight + Waveshare 一键启动 (Windows cmd / 双击可运行)
REM
REM 同时拉起 3 个进程:
REM   1) InkSight Backend (FastAPI, 端口 8080)
REM   2) Waveshare Bridge   (FastAPI + TCP 6868, 端口 9000)
REM   3) Webapp             (Next.js,   端口 3000)
REM
REM 使用方法:
REM   1) 把下面 DEVICE_HOST 改成你电脑的局域网 IP (ipconfig 查看)
REM   2) 双击本文件
REM   3) 浏览器打开 http://127.0.0.1:3000/cloud-module
REM
REM 关闭时直接关掉弹出的 3 个黑色窗口即可

REM === 你需要改的唯一配置: 你电脑的局域网 IP ===
set DEVICE_HOST=192.168.1.195
set BRIDGE_HTTP_PORT=9000
set BACKEND_PORT=8080

set REPO_ROOT=%~dp0
set BACKEND_DIR=%REPO_ROOT%backend
set WEBAPP_DIR=%REPO_ROOT%webapp

REM 找 venv (优先 backend\.venv)
if exist "%BACKEND_DIR%\.venv\Scripts\python.exe" (
    set PYTHON_EXE=%BACKEND_DIR%\.venv\Scripts\python.exe
) else (
    set PYTHON_EXE=python
)

echo.
echo ============================================
echo  InkSight + Waveshare Bridge 启动器
echo  后端:  http://127.0.0.1:%BACKEND_PORT%
echo  Bridge: http://127.0.0.1:%BRIDGE_HTTP_PORT%  + TCP 6868
echo  Web:    http://127.0.0.1:3000
echo  目标主机: %DEVICE_HOST%
echo ============================================
echo.

REM === 1) InkSight Backend ===
echo [1/3] 启动 InkSight Backend (端口 %BACKEND_PORT%)...
start "InkSight-Backend" cmd /k "cd /d %BACKEND_DIR% && %PYTHON_EXE% -m uvicorn api.index:app --host 0.0.0.0 --port %BACKEND_PORT%"

REM === 2) Waveshare Bridge ===
echo [2/3] 启动 Waveshare Bridge (HTTP %BRIDGE_HTTP_PORT% + TCP 6868)...
start "Waveshare-Bridge" cmd /k "cd /d %BACKEND_DIR% && set BRIDGE_LOG_FILE=%BACKEND_DIR%\bridge.log && %PYTHON_EXE% -m backend.scripts.waveshare_bridge --device-ip %DEVICE_HOST% --port %BRIDGE_HTTP_PORT%"

REM === 3) Webapp ===
echo [3/3] 启动 Webapp (端口 3000)...
start "InkSight-Webapp" cmd /k "cd /d %WEBAPP_DIR% && npx next dev -p 3000"

echo.
echo 三个服务已启动, 等待 10 秒加载...
echo 然后浏览器打开: http://127.0.0.1:3000/cloud-module
echo.

REM 等 10 秒让 next dev 起来, 然后用默认浏览器打开 webapp
timeout /t 10 /nobreak >nul
start "" http://127.0.0.1:3000/cloud-module

echo.
echo 关闭: 直接关掉那 3 个黑色窗口
pause
