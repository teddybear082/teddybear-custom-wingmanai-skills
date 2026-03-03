from typing import TYPE_CHECKING
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from api.enums import LogType
from skills.skill_base import Skill, tool
import os
import aiohttp
import asyncio
import sys
import shutil

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman


class FFmpegTools(Skill):
    """Download FFmpeg and provide tools for common audio and video processing tasks like converting formats, extracting audio, trimming videos, and more."""

    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.ffmpeg_path = None
        self.generated_dir = None

    async def validate(self) -> list[WingmanInitializationError]:
        """Validate configuration and check FFmpeg availability."""
        errors = await super().validate()
        
        # Validate custom properties exist (don't cache the values!)
        self.retrieve_custom_property_value("ffmpeg_download_url", errors)
        
        return errors

    async def prepare(self) -> None:
        """Initialize FFmpeg - detect OS and find/download executable."""
        await super().prepare()
        
        # Get the generated files directory for this skill
        self.generated_dir = self.get_generated_files_dir()
        
        if sys.platform == "darwin":
            # macOS: Look for ffmpeg in PATH or common Homebrew locations
            ffmpeg_path = shutil.which("ffmpeg")
            if not ffmpeg_path:
                # Check common Homebrew paths
                common_paths = [
                    "/usr/local/bin/ffmpeg",  # Intel Macs
                    "/opt/homebrew/bin/ffmpeg",  # Apple Silicon Macs
                ]
                for p in common_paths:
                    if os.path.exists(p):
                        ffmpeg_path = p
                        break
            
            self.ffmpeg_path = ffmpeg_path
            if self.ffmpeg_path:
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"FFmpeg found on macOS at: {self.ffmpeg_path}",
                        color=LogType.INFO,
                    )
            else:
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        "FFmpeg NOT found on macOS. It must be installed via brew.",
                        color=LogType.WARNING,
                    )
        else:
            # Windows/Default: download if not present
            self.ffmpeg_exe_path = os.path.join(self.generated_dir, "ffmpeg.exe")
            
            # Check if FFmpeg exists, if not download it
            if not os.path.exists(self.ffmpeg_exe_path):
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        "FFmpeg not found, downloading...",
                        color=LogType.INFO,
                    )
                await self._download_ffmpeg()
            
            self.ffmpeg_path = self.ffmpeg_exe_path
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"FFmpeg ready at: {self.ffmpeg_path}",
                    color=LogType.INFO,
                )

    async def _download_ffmpeg(self) -> None:
        """Download FFmpeg from the configured URL."""
        errors = []
        download_url = self.retrieve_custom_property_value("ffmpeg_download_url", errors)
        
        if not download_url:
            download_url = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
        
        os.makedirs(self.generated_dir, exist_ok=True)
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(download_url) as response:
                    if response.status == 200:
                        zip_path = os.path.join(self.generated_dir, "ffmpeg.zip")
                        
                        # Save the zip file
                        with open(zip_path, 'wb') as f:
                            f.write(await response.read())
                        
                        # Extract the zip file
                        import zipfile
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            zip_ref.extractall(self.generated_dir)
                        
                        # Find ffmpeg.exe in extracted contents
                        for root, dirs, files in os.walk(self.generated_dir):
                            if "ffmpeg.exe" in files:
                                # Move ffmpeg.exe to the skill's generated directory
                                src_path = os.path.join(root, "ffmpeg.exe")
                                if src_path != self.ffmpeg_exe_path:
                                    import shutil
                                    shutil.move(src_path, self.ffmpeg_exe_path)
                                break
                        
                        # Clean up zip file
                        os.remove(zip_path)
                        
                        # Clean up extracted folders (keep only ffmpeg.exe)
                        for item in os.listdir(self.generated_dir):
                            item_path = os.path.join(self.generated_dir, item)
                            if os.path.isdir(item_path) and item != "__pycache__":
                                import shutil
                                shutil.rmtree(item_path)
                        
                        if self.settings.debug_mode:
                            await self.printr.print_async(
                                "FFmpeg downloaded successfully!",
                                color=LogType.INFO,
                            )
                    else:
                        if self.settings.debug_mode:
                            await self.printr.print_async(
                                f"Failed to download FFmpeg: {response.status}",
                                color=LogType.ERROR,
                            )
        except Exception as e:
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Error downloading FFmpeg: {str(e)}",
                    color=LogType.ERROR,
                )
            raise

    async def _run_ffmpeg(self, args: list[str]) -> tuple[str, str, int]:
        """Run FFmpeg command and return stdout, stderr, and return code."""
        if not self.ffmpeg_path:
            error_msg = "No ffmpeg found on system. Download ffmpeg for MacOS by using brew install ffmpeg to use this skill."
            return "", error_msg, 1

        cmd = [self.ffmpeg_path] + args
        
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        return stdout.decode(), stderr.decode(), process.returncode

    @tool(
        name="convert_audio",
        description="""Convert audio files between different formats (MP3, WAV, AAC, FLAC, etc.).

        WHEN TO USE:
        - User wants to convert an audio file from one format to another
        - User needs to extract audio from a video file
        - Common requests: "Convert this to MP3", "Change this to WAV", "Extract audio from video"
        
        Returns the path to the converted audio file.""",
        wait_response=True
    )
    async def convert_audio(
        self,
        input_path: str,
        output_path: str,
        output_format: str = "mp3"
    ) -> str:
        """
        Convert an audio file to a different format.
        
        Args:
            input_path: Full path to the input audio file
            output_path: Full path where the output file should be saved
            output_format: Target format (mp3, wav, aac, flac, ogg, m4a). Default is mp3.
        """
        try:
            args = ["-y", "-i", input_path]
            
            # Add format-specific options
            if output_format.lower() == "mp3":
                args.extend(["-codec:a", "libmp3lame", "-b:a", "192k"])
            elif output_format.lower() == "wav":
                args.extend(["-codec:a", "pcm_s16le"])
            elif output_format.lower() == "aac":
                args.extend(["-codec:a", "aac", "-b:a", "192k"])
            elif output_format.lower() == "flac":
                args.extend(["-codec:a", "flac"])
            elif output_format.lower() == "ogg":
                args.extend(["-codec:a", "libvorbis", "-q:a", "5"])
            elif output_format.lower() == "m4a":
                args.extend(["-codec:a", "aac", "-b:a", "256k"])
            
            args.append(output_path)
            
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully converted audio to {output_path}"
            else:
                return f"Conversion failed: {stderr}"
        except Exception as e:
            return f"Error converting audio: {str(e)}"

    @tool(
        name="extract_audio_from_video",
        description="""Extract audio track from a video file and save it as a separate audio file.

        WHEN TO USE:
        - User wants to get just the audio from a video
        - Common requests: "Extract audio from this video", "Get the soundtrack from this file"
        
        Returns the path to the extracted audio file.""",
        wait_response=True
    )
    async def extract_audio_from_video(
        self,
        video_path: str,
        output_path: str,
        format: str = "mp3"
    ) -> str:
        """
        Extract audio from a video file.
        
        Args:
            video_path: Full path to the input video file
            output_path: Full path where the extracted audio should be saved
            format: Audio format for output (mp3, wav, aac, flac). Default is mp3.
        """
        try:
            args = [
                "-y", "-i", video_path,
                "-vn",  # No video
                "-acodec", "libmp3lame" if format == "mp3" else "pcm_s16le" if format == "wav" else "aac",
                "-ab", "192k",
                output_path
            ]
            
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully extracted audio to {output_path}"
            else:
                return f"Extraction failed: {stderr}"
        except Exception as e:
            return f"Error extracting audio: {str(e)}"

    @tool(
        name="trim_audio",
        description="""Trim or cut an audio file to a specific duration starting from a given time.

        WHEN TO USE:
        - User wants to shorten an audio file
        - User needs to remove beginning or end portions
        - Common requests: "Trim this audio", "Cut the first 30 seconds", "Keep only 2 minutes from the start"
        
        Returns the path to the trimmed audio file.""",
        wait_response=True
    )
    async def trim_audio(
        self,
        input_path: str,
        output_path: str,
        start_time: str,
        duration: str
    ) -> str:
        """
        Trim an audio file.
        
        Args:
            input_path: Full path to the input audio file
            output_path: Full path where the trimmed audio should be saved
            start_time: Start time in HH:MM:SS or SS.S format (e.g., "00:00:30" or "30.5")
            duration: Duration to keep in HH:MM:SS or SS.S format (e.g., "00:02:00" or "120")
        """
        try:
            args = [
                "-y", "-i", input_path,
                "-ss", start_time,
                "-t", duration,
                "-codec", "copy",
                output_path
            ]
            
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully trimmed audio to {output_path}"
            else:
                return f"Trimming failed: {stderr}"
        except Exception as e:
            return f"Error trimming audio: {str(e)}"

    @tool(
        name="compress_audio",
        description="""Compress an audio file to reduce its file size while maintaining reasonable quality.

        WHEN TO USE:
        - User wants to reduce the file size of an audio file
        - User needs to make audio suitable for sharing or streaming
        - Common requests: "Make this file smaller", "Compress this audio", "Reduce the size"
        
        Returns the path to the compressed audio file.""",
        wait_response=True
    )
    async def compress_audio(
        self,
        input_path: str,
        output_path: str,
        bitrate: str = "128k"
    ) -> str:
        """
        Compress an audio file to reduce file size.
        
        Args:
            input_path: Full path to the input audio file
            output_path: Full path where the compressed audio should be saved
            bitrate: Target bitrate (64k, 96k, 128k, 192k, etc.). Lower = smaller file. Default is 128k.
        """
        try:
            args = [
                "-y", "-i", input_path,
                "-codec:a", "libmp3lame",
                "-b:a", bitrate,
                output_path
            ]
            
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully compressed audio to {output_path} at {bitrate}"
            else:
                return f"Compression failed: {stderr}"
        except Exception as e:
            return f"Error compressing audio: {str(e)}"

    @tool(
        name="convert_stereo_to_mono",
        description="""Convert a stereo audio file to mono (single channel).
        
        WHEN TO USE:
        - User wants to reduce channels or specifically needs a mono file.
        - Common requests: "Make this mono", "Convert to single channel"
        
        Returns the path to the mono audio file.""",
        wait_response=True
    )
    async def convert_stereo_to_mono(
        self,
        input_path: str,
        output_path: str
    ) -> str:
        """
        Convert stereo audio to mono.
        
        Args:
            input_path: Full path to the input audio file
            output_path: Full path where the mono audio should be saved
        """
        try:
            args = ["-y", "-i", input_path, "-ac", "1", output_path]
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully converted to mono: {output_path}"
            else:
                return f"Conversion to mono failed: {stderr}"
        except Exception as e:
            return f"Error converting to mono: {str(e)}"

    @tool(
        name="change_sample_rate",
        description="""Change the sample rate of an audio file (e.g., 44100Hz to 22050Hz).
        
        WHEN TO USE:
        - User wants to change the audio quality or compatibility by adjusting sample rate.
        - Common requests: "Change sample rate to 22050", "Convert 44kz to 22kz"
        
        Returns the path to the modified audio file.""",
        wait_response=True
    )
    async def change_sample_rate(
        self,
        input_path: str,
        output_path: str,
        sample_rate: str = "22050"
    ) -> str:
        """
        Change audio sample rate.
        
        Args:
            input_path: Full path to the input audio file
            output_path: Full path where the output should be saved
            sample_rate: Target sample rate in Hz (e.g., "44100", "22050", "16000"). Default is 22050.
        """
        try:
            args = ["-y", "-i", input_path, "-ar", sample_rate, output_path]
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            if returncode == 0:
                return f"Successfully changed sample rate to {sample_rate}: {output_path}"
            else:
                return f"Failed to change sample rate: {stderr}"
        except Exception as e:
            return f"Error changing sample rate: {str(e)}"

    @tool(
        name="get_media_info",
        description="""Get detailed information about an audio or video file including duration, format, bitrate, resolution, codecs, etc.

        WHEN TO USE:
        - User wants to know the properties of a media file
        - User needs to check duration, format, or technical details
        - Common requests: "What's the duration of this file?", "Tell me about this video", "What format is this?"
        
        Returns detailed information about the media file.""",
        wait_response=False
    )
    async def get_media_info(self, media_path: str) -> str:
        """
        Get detailed information about a media file.
        
        Args:
            media_path: Full path to the audio or video file
        """
        try:
            args = ["-i", media_path]
            
            stdout, stderr, returncode = await self._run_ffmpeg(args)
            
            # FFmpeg outputs info to stderr
            return f"Media Information:\n{stderr}"
        except Exception as e:
            return f"Error getting media info: {str(e)}"

    async def unload(self) -> None:
        """Clean up resources when skill is unloaded."""
        await super().unload()
