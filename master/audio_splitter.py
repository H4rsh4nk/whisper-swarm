"""
Audio splitter - splits audiobooks into chunks for distributed processing.
Uses ffmpeg for audio manipulation.

Optimized for minimal bandwidth:
- MP3 format at 48kbps (speech-optimized, ~7MB per 20-min chunk vs 38MB for WAV)
- 16kHz mono (optimal for Whisper)
"""

import asyncio
import subprocess
import json
from pathlib import Path


class AudioSplitter:
    def __init__(self, output_dir: Path, chunk_duration: int = 1200, 
                 audio_format: str = "mp3", bitrate: str = "48k"):
        """
        Initialize the audio splitter.

        Args:
            output_dir: Directory to store audio chunks
            chunk_duration: Duration of each chunk in seconds (default 20 minutes)
            audio_format: Output format - "mp3" (small) or "wav" (lossless)
            bitrate: Bitrate for compressed formats (default 48k, good for speech)
        """
        self.output_dir = Path(output_dir)
        self.chunk_duration = chunk_duration
        self.audio_format = audio_format
        self.bitrate = bitrate
        self.output_dir.mkdir(exist_ok=True)

    async def get_audio_duration(self, audio_path: Path) -> float:
        """Get the duration of an audio file in seconds."""
        cmd = [
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            str(audio_path)
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await proc.communicate()

        info = json.loads(stdout.decode())
        return float(info["format"]["duration"])

    async def split_audio(self, audio_path: Path, book_id: str) -> list[dict]:
        """
        Split an audio file into chunks.

        Returns a list of chunk info dictionaries.
        """
        duration = await self.get_audio_duration(audio_path)
        chunks = []

        # Calculate number of chunks
        num_chunks = int(duration // self.chunk_duration) + 1

        tasks = []
        for i in range(num_chunks):
            start_time = i * self.chunk_duration
            end_time = min((i + 1) * self.chunk_duration, duration)

            chunk_id = f"chunk_{i:04d}"
            chunk_filename = f"{book_id}_{chunk_id}.{self.audio_format}"
            chunk_path = self.output_dir / chunk_filename

            tasks.append(self._extract_chunk(
                audio_path, chunk_path, start_time, end_time - start_time
            ))

            chunks.append({
                "chunk_id": chunk_id,
                "path": str(chunk_path),
                "filename": chunk_filename,
                "start": start_time,
                "end": end_time,
                "duration": end_time - start_time
            })

        # Process chunks in parallel (limit concurrency)
        semaphore = asyncio.Semaphore(4)

        async def limited_extract(task):
            async with semaphore:
                return await task

        await asyncio.gather(*[limited_extract(t) for t in tasks])

        return chunks

    async def _extract_chunk(self, input_path: Path, output_path: Path,
                             start: float, duration: float):
        """Extract a chunk from the audio file."""
        # Base command
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output
            "-ss", str(start),  # Seek before input (faster)
            "-i", str(input_path),
            "-t", str(duration),
            "-ar", "16000",  # 16kHz sample rate (optimal for Whisper)
            "-ac", "1",  # Mono
        ]

        # Format-specific encoding
        if self.audio_format == "mp3":
            cmd.extend([
                "-c:a", "libmp3lame",
                "-b:a", self.bitrate,  # e.g., "48k" - good for speech
            ])
        elif self.audio_format == "opus":
            cmd.extend([
                "-c:a", "libopus",
                "-b:a", self.bitrate,
            ])
        else:  # wav (fallback)
            cmd.extend([
                "-c:a", "pcm_s16le",  # 16-bit PCM
            ])

        cmd.append(str(output_path))

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL
        )
        await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to extract chunk: {output_path}")
