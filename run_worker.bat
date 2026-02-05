@echo off
setlocal enabledelayedexpansion
echo ============================================
echo   Distributed STT - Worker Client
echo ============================================
echo.

cd /d "%~dp0"

REM Check if venv exists
if not exist "venv_worker" (
    echo Creating virtual environment...
    python -m venv venv_worker
)

REM Activate venv
call venv_worker\Scripts\activate.bat

REM Check if torch is installed
python -c "import torch" 2>nul
if errorlevel 1 (
    echo.
    echo PyTorch not installed. Select your setup:
    echo [1] NVIDIA GPU with CUDA 12.1 ^(recommended for newer GPUs^)
    echo [2] NVIDIA GPU with CUDA 11.8 ^(for older GPUs^)
    echo [3] CPU only ^(slower, but works everywhere^)
    echo.
    set /p CUDA_CHOICE="Enter choice (1/2/3): "

    if "!CUDA_CHOICE!"=="1" (
        echo Installing PyTorch with CUDA 12.1...
        pip install torch --index-url https://download.pytorch.org/whl/cu121
    ) else if "!CUDA_CHOICE!"=="2" (
        echo Installing PyTorch with CUDA 11.8...
        pip install torch --index-url https://download.pytorch.org/whl/cu118
    ) else (
        echo Installing PyTorch CPU version...
        pip install torch
    )
)

REM Install other dependencies
echo Installing dependencies...
pip install faster-whisper httpx websockets -q

REM Show GPU status
echo.
echo ============================================
echo   GPU Detection
echo ============================================
python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None - using CPU\"}')"
echo ============================================
echo.

REM Run worker
python worker\worker.py %1

pause
