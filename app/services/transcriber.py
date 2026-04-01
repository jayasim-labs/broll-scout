"""
Transcriber Service - Transcribes audio from video files using Whisper
Handles file downloads, audio extraction, and Whisper API calls
"""

import os
import tempfile
import subprocess
from typing import Optional, Tuple
from pathlib import Path
import httpx

from app.config import get_settings
from app.models.schemas import TranscriptionResult, TranscriptionSegment
from app.utils.cost_tracker import get_cost_tracker


class TranscriberService:
    """Transcribes audio from video files using OpenAI Whisper."""
    
    def __init__(self):
        cfg = get_settings()
        self.api_key = cfg.openai_api_key
        self.api_url = "https://api.openai.com/v1/audio/transcriptions"
        self.cost_tracker = get_cost_tracker()
        self.supported_formats = ['.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm']
        self.max_file_size = 25 * 1024 * 1024  # 25MB Whisper limit
    
    async def transcribe_video(
        self,
        video_url: str,
        language: str = "en",
        job_id: Optional[str] = None
    ) -> TranscriptionResult:
        """
        Transcribe audio from a video URL.
        
        Args:
            video_url: URL of the video to transcribe
            language: Language code (e.g., 'en', 'es', 'fr')
            job_id: Optional job ID for cost tracking
            
        Returns:
            TranscriptionResult with text and segments
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
        temp_dir = tempfile.mkdtemp()
        video_path = None
        audio_path = None
        
        try:
            # Download video
            video_path = await self._download_video(video_url, temp_dir)
            
            # Extract audio
            audio_path = await self._extract_audio(video_path, temp_dir)
            
            # Check file size
            file_size = os.path.getsize(audio_path)
            if file_size > self.max_file_size:
                # Compress audio if too large
                audio_path = await self._compress_audio(audio_path, temp_dir)
            
            # Transcribe with Whisper
            result = await self._transcribe_audio(audio_path, language)
            
            # Calculate duration from segments
            duration = 0.0
            if result.segments:
                duration = max(seg.end for seg in result.segments)
            
            minutes = duration / 60.0
            if job_id:
                self.cost_tracker.track_whisper(job_id, minutes)
            
            result.cost = minutes * 0.006
            result.duration = duration
            
            return result
            
        finally:
            # Cleanup temp files
            self._cleanup_temp_files(temp_dir)
    
    async def transcribe_file(
        self,
        file_path: str,
        language: str = "en",
        job_id: Optional[str] = None
    ) -> TranscriptionResult:
        """
        Transcribe audio from a local file.
        
        Args:
            file_path: Path to the audio/video file
            language: Language code
            job_id: Optional job ID for cost tracking
            
        Returns:
            TranscriptionResult with text and segments
        """
        if not self.api_key:
            raise ValueError("OpenAI API key not configured")
        
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        temp_dir = tempfile.mkdtemp()
        audio_path = file_path
        
        try:
            # Extract audio if it's a video file
            if path.suffix.lower() in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
                audio_path = await self._extract_audio(file_path, temp_dir)
            
            # Check file size and compress if needed
            file_size = os.path.getsize(audio_path)
            if file_size > self.max_file_size:
                audio_path = await self._compress_audio(audio_path, temp_dir)
            
            # Transcribe
            result = await self._transcribe_audio(audio_path, language)
            
            # Calculate costs
            duration = 0.0
            if result.segments:
                duration = max(seg.end for seg in result.segments)
            
            minutes = duration / 60.0
            if job_id:
                self.cost_tracker.track_whisper(job_id, minutes)
            
            result.cost = minutes * 0.006
            result.duration = duration
            
            return result
            
        finally:
            self._cleanup_temp_files(temp_dir)
    
    async def _download_video(self, url: str, temp_dir: str) -> str:
        """Download video from URL to temp directory."""
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Determine file extension from content type
            content_type = response.headers.get("content-type", "video/mp4")
            ext = ".mp4"
            if "webm" in content_type:
                ext = ".webm"
            elif "mov" in content_type:
                ext = ".mov"
            
            video_path = os.path.join(temp_dir, f"video{ext}")
            
            with open(video_path, "wb") as f:
                f.write(response.content)
            
            return video_path
    
    async def _extract_audio(self, video_path: str, temp_dir: str) -> str:
        """Extract audio from video using ffmpeg."""
        audio_path = os.path.join(temp_dir, "audio.mp3")
        
        # Use ffmpeg to extract audio
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vn",  # No video
            "-acodec", "libmp3lame",
            "-ar", "16000",  # 16kHz sample rate (Whisper optimal)
            "-ac", "1",  # Mono
            "-b:a", "64k",  # Bitrate
            "-y",  # Overwrite
            audio_path
        ]
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        
        if process.returncode != 0:
            raise Exception(f"ffmpeg audio extraction failed: {process.stderr}")
        
        return audio_path
    
    async def _compress_audio(self, audio_path: str, temp_dir: str) -> str:
        """Compress audio to fit within Whisper's size limit."""
        compressed_path = os.path.join(temp_dir, "audio_compressed.mp3")
        
        cmd = [
            "ffmpeg", "-i", audio_path,
            "-ar", "16000",
            "-ac", "1",
            "-b:a", "32k",  # Lower bitrate
            "-y",
            compressed_path
        ]
        
        process = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )
        
        if process.returncode != 0:
            raise Exception(f"ffmpeg compression failed: {process.stderr}")
        
        return compressed_path
    
    async def _transcribe_audio(self, audio_path: str, language: str) -> TranscriptionResult:
        """Send audio to Whisper API for transcription."""
        async with httpx.AsyncClient(timeout=300.0) as client:
            with open(audio_path, "rb") as audio_file:
                files = {"file": ("audio.mp3", audio_file, "audio/mpeg")}
                data = {
                    "model": "whisper-1",
                    "language": language,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "segment"
                }
                
                response = await client.post(
                    self.api_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files=files,
                    data=data
                )
                response.raise_for_status()
                result = response.json()
        
        # Parse segments
        segments = []
        for seg in result.get("segments", []):
            segment = TranscriptionSegment(
                id=seg.get("id", 0),
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                text=seg.get("text", "").strip()
            )
            segments.append(segment)
        
        return TranscriptionResult(
            text=result.get("text", ""),
            segments=segments,
            language=result.get("language", language),
            duration=result.get("duration", 0.0),
            cost=0.0  # Will be calculated by caller
        )
    
    def _cleanup_temp_files(self, temp_dir: str):
        """Clean up temporary files."""
        import shutil
        try:
            shutil.rmtree(temp_dir)
        except Exception:
            pass  # Best effort cleanup


# Singleton instance
transcriber_service = TranscriberService()
