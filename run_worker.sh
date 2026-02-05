#!/bin/bash
echo "============================================"
echo "  Distributed STT - Worker Client"
echo "============================================"
echo

cd "$(dirname "$0")"

# Check if venv exists
if [ ! -d "venv_worker" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv_worker
fi

# Activate venv
source venv_worker/bin/activate

# Check if torch is installed
if ! python -c "import torch" 2>/dev/null; then
    echo
    echo "PyTorch not installed. Select your setup:"
    echo "[1] NVIDIA GPU with CUDA 12.1 (recommended for newer GPUs)"
    echo "[2] NVIDIA GPU with CUDA 11.8 (for older GPUs)"
    echo "[3] CPU only (works on Mac/Linux without NVIDIA GPU)"
    echo
    read -p "Enter choice (1/2/3): " CUDA_CHOICE

    case $CUDA_CHOICE in
        1)
            echo "Installing PyTorch with CUDA 12.1..."
            pip install torch --index-url https://download.pytorch.org/whl/cu121
            ;;
        2)
            echo "Installing PyTorch with CUDA 11.8..."
            pip install torch --index-url https://download.pytorch.org/whl/cu118
            ;;
        *)
            echo "Installing PyTorch CPU version..."
            pip install torch
            ;;
    esac
fi

# Install other dependencies
echo "Installing dependencies..."
pip install faster-whisper httpx websockets -q

# Show GPU status
echo
echo "============================================"
echo "  GPU Detection"
echo "============================================"
python -c "import torch; print(f'CUDA Available: {torch.cuda.is_available()}'); print(f'GPU: {torch.cuda.get_device_name(0) if torch.cuda.is_available() else \"None - using CPU\"}')"
echo "============================================"
echo

# Run worker
python worker/worker.py "$1"
