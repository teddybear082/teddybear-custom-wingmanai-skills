import os
import re
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING
from pytubefix.contrib.search import Search, Filter
from pytubefix import YouTube
from youtube_transcript_api import YouTubeTranscriptApi
from api.enums import LogType
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill
from services.file import get_writable_dir

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

class YouTubeAssistant(Skill):
    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config, settings, wingman)
        self.downloads_directory = get_writable_dir(os.path.join("skills", "youtube_assistant", "downloads"))

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "search_videos",
                {
                    "type": "function",
                    "function": {
                        "name": "search_videos",
                        "description": "Search videos on YouTube for a given topic with optional filters.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "topic": {"type": "string", "description": "The topic to search for."},
                                "max_results": {"type": "integer", "description": "The number of search results to return (default 3)."},
                                "upload_date": {"type": "string", "description": "Filter by upload date.", "enum": ["Last Hour", "Today", "This Week", "This Month", "This Year"]},
                                "type": {"type": "string", "description": "Filter by type.", "enum": ["Video", "Channel", "Playlist", "Movie"]},
                                "duration": {"type": "string", "description": "Filter by duration.", "enum": ["Under 4 minutes", "Over 20 minutes", "4 - 20 minutes"]},
                                "features": {"type": "array", "description": "Filter by features.", "items": {"type": "string", "enum": ["Live", "4K", "HD", "Subtitles/CC", "Creative Commons", "360", "VR180", "3D", "HDR", "Location", "Purchased"]}},
                                "sort_by": {"type": "string", "description": "Sort by criteria.", "enum": ["Relevance", "Upload date", "View count", "Rating"]}
                            },
                            "required": ["topic"],
                        },
                    },
                },
            ),
            (
                "get_transcript",
                {
                    "type": "function",
                    "function": {
                        "name": "get_transcript",
                        "description": "Get the plain text transcript of a YouTube video from a given URL or video ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "youtube_url_or_id": {"type": "string", "description": "The URL or the video ID of the YouTube video."},
                                "language_code": {"type": "string", "description": "The optional two-letter language code for the language the user desires the transcript in."},
                            },
                            "required": ["youtube_url_or_id"],
                        },
                    },
                },
            ),
            (
                "download_audio",
                {
                    "type": "function",
                    "function": {
                        "name": "download_audio",
                        "description": "Download the audio of a YouTube video from a given URL or video ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "youtube_url_or_id": {"type": "string", "description": "The URL or the video ID of the YouTube video."},
                                "directory": {"type": "string", "description": "The directory to save the audio file in."},
                                "file_name": {"type": "string", "description": "The name of the audio file."},
                            },
                            "required": ["youtube_url_or_id", "directory"],
                        },
                    },
                },
            ),
            (
                "download_video",
                {
                    "type": "function",
                    "function": {
                        "name": "download_video",
                        "description": "Download the video of a YouTube video from a given URL or video ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "youtube_url_or_id": {"type": "string", "description": "The URL or the video ID of the YouTube video."},
                                "directory": {"type": "string", "description": "The directory to save the video file in."},
                                "file_name": {"type": "string", "description": "The name of the video file."},
                            },
                            "required": ["youtube_url_or_id", "directory"],
                        },
                    },
                },
            ),
        ]
        return tools

    def extract_video_id(self, youtube_url_or_id):
        if "watch" in youtube_url_or_id:
            url_data = re.findall(r"v=([^&]+)", youtube_url_or_id)
            video_id = url_data[0] if url_data else youtube_url_or_id
        else:
            video_id = youtube_url_or_id
        return video_id

    async def search_videos(self, parameters):
        topic = parameters["topic"]
        max_results = parameters.get("max_results", 3)
        filters = {}
        if "upload_date" in parameters:
            filters['upload_date'] = Filter.get_upload_data(parameters["upload_date"])
        if "type" in parameters:
            filters['type'] = Filter.get_type(parameters["type"])
        if "duration" in parameters:
            filters['duration'] = Filter.get_duration(parameters["duration"])
        if "features" in parameters:
            filters['features'] = [Filter.get_features(feature) for feature in parameters["features"]]
        if "sort_by" in parameters:
            filters['sort_by'] = Filter.get_sort_by(parameters["sort_by"])
        try:
            s = Search(topic, filters=filters)
            videos = s.videos[:max_results] # could also add s.shorts, channels, etc. I believe
            video_data = [
                (video.title, video.watch_url, video.publish_date.strftime('%B %d, %Y'))
                for video in videos
            ]
            return {"videos": video_data}
        except Exception as e:
            return {"status": f"Something happened with the search. Error was {e}."}

    async def get_transcript(self, parameters):
        video_id = self.extract_video_id(parameters["youtube_url_or_id"])
        language_code = parameters.get("language_code", 'en')
        languages = [language_code.lower(), 'en'] if language_code and language_code != 'en' else ['en']
        try:
            transcript = YouTubeTranscriptApi.get_transcript(video_id, languages=languages)
            transcript_text = "\n".join([text['text'] for text in transcript])
            return {"transcript": transcript_text}
        except Exception as e:
            return {"transcript": f"Error retrieving transcript: {str(e)}"}

    async def download_audio(self, parameters):
        video_id = self.extract_video_id(parameters["youtube_url_or_id"])
        file_name = parameters.get("file_name", f"{video_id}.mp3")
        directory = parameters.get("directory", self.downloads_directory)
        if directory == "." or directory == "./":
            directory = self.downloads_directory
        try:
            yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
            stream = yt.streams.filter(only_audio=True).first()
            stream.download(directory, filename=file_name)
            return {"status": f"Audio downloaded to directory: {directory} with file name: {file_name}."}
        except Exception as e:
            return {"status": f"There was a problem downloading the audio. The error was {e}."}

    async def download_video(self, parameters):
        video_id = self.extract_video_id(parameters["youtube_url_or_id"])
        file_name = parameters.get("file_name", f"{video_id}.mp4")
        directory = parameters.get("directory", self.downloads_directory)
        if directory == "." or directory == "./":
            directory = self.downloads_directory
        try:
            yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
            stream = yt.streams.filter(file_extension="mp4", res="720p", progressive=True).first()
            if not stream:
                stream = yt.streams.filter(file_extension="mp4", progressive=True).first()
            stream.download(output_path=directory, filename=file_name)
            return {"status": f"Video downloaded to directory: {directory} with file name: {file_name}."}
        except Exception as e:
            return {"status": f"There was a problem downloading the video. The error was {e}."}

    async def execute_tool(self, tool_name: str, parameters: dict):
        function_response = "There was a problem with performing this action for YouTube. Could not perform action."
        instant_response = ""
        if self.settings.debug_mode:
            self.start_execution_benchmark()
            await self.printr.print_async(
                f"Executing {tool_name} function with parameters: {parameters}",
                color=LogType.INFO,
            )
        if tool_name == "search_videos":
            function_response = await self.search_videos(parameters)
        elif tool_name == "get_transcript":
            function_response = await self.get_transcript(parameters)
        elif tool_name == "download_audio":
            function_response = await self.download_audio(parameters)
        elif tool_name == "download_video":
            function_response = await self.download_video(parameters)
        if self.settings.debug_mode:
            await self.printr.print_async(
                f"Function response: {function_response}",
                color=LogType.INFO,
            )
            await self.print_execution_time()
        return function_response, instant_response