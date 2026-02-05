"""
Build script to create standalone worker executable.
Run this on Windows to generate worker.exe
"""

import PyInstaller.__main__
import os
import sys

# Get the directory where this script is located
script_dir = os.path.dirname(os.path.abspath(__file__))
worker_script = os.path.join(script_dir, "worker.py")

PyInstaller.__main__.run([
    worker_script,
    '--onefile',
    '--name=stt_worker',
    '--console',
    # Include faster-whisper assets
    '--collect-all=faster_whisper',
    '--collect-all=ctranslate2',
    # Hidden imports
    '--hidden-import=torch',
    '--hidden-import=torchaudio',
    '--hidden-import=numpy',
    '--hidden-import=httpx',
    '--hidden-import=websockets',
    # Clean build
    '--clean',
])

print("\n" + "="*50)
print("Build complete!")
print("Executable: dist/stt_worker.exe")
print("="*50)
