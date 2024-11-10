import os
import re
import subprocess
import json
from typing import TYPE_CHECKING
from pytubefix.contrib.search import Search, Filter
from api.enums import LogType
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill
from services.file import get_writable_dir

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

class YouTubeAssistantDLP(Skill):
    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config, settings, wingman)
        self.yt_dlp_directory = get_writable_dir(os.path.join("skills", "youtube_assistant_yt_dlp"))
        self.downloads_directory = get_writable_dir(os.path.join("skills", "youtube_assistant_yt_dlp", "downloads"))
        self.yt_dlp_path = os.path.join(self.yt_dlp_directory, "yt-dlp.exe")

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
                "download_audio",
                {
                    "type": "function",
                    "function": {
                        "name": "download_audio",
                        "description": "Download the audio of a online video from a given URL.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "video_url": {"type": "string", "description": "The URL of the online video."},
                                "directory": {"type": "string", "description": "The directory to save the audio file in."},
                                "file_name": {"type": "string", "description": "The name of the audio file."},
                            },
                            "required": ["video_url", "directory"],
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
                                "video_url": {"type": "string", "description": "The URL of the online video."},
                                "directory": {"type": "string", "description": "The directory to save the video file in."},
                                "file_name": {"type": "string", "description": "The name of the video file."},
                            },
                            "required": ["video_url", "directory"],
                        },
                    },
                },
            ),
            (
                "download_subtitles_and_convert",
                {
                    "type": "function",
                    "function": {
                        "name": "download_subtitles_and_convert",
                        "description": "Download the subtitles of a YouTube video and convert it to plain text.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "video_url": {"type": "string", "description": "The URL of the online video."},
                            },
                            "required": ["video_url"],
                        },
                    },
                },
            ),
            (
                "get_video_comments",
                {
                    "type": "function",
                    "function": {
                        "name": "get_video_comments",
                        "description": "Retrieve the commemts of viewers of a video.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "video_url": {"type": "string", "description": "The URL of the online video."},
                            },
                            "required": ["video_url"],
                        },
                    },
                },
            ),
        ]
        return tools

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
            videos = s.videos[:max_results]
            video_data = [
                (video.title, video.watch_url, video.publish_date.strftime('%B %d, %Y'))
                for video in videos
            ]
            return {"videos": video_data}
        except Exception as e:
            return {"status": f"Something happened with the search. Error was {e}."}

    async def download_with_ytdlp(self, parameters, format_type):
        video_url = parameters.get("video_url")
        if not video_url:
            return {"status": "Error: no video_url provided"}
        directory = parameters.get("directory")
        if not directory or directory == None or directory == "." or directory == "./" or directory == "../":
            directory = self.downloads_directory
        yt_dlp_command = [
            self.yt_dlp_path, 
            "-f", format_type, 
            "-P", directory, 
            video_url
        ]
        file_name = parameters.get("file_name")
        if file_name:
            yt_dlp_command.extend(["-o", file_name])
        process = subprocess.run(yt_dlp_command, capture_output=True, text=True, cwd=self.yt_dlp_directory)
        if process.returncode == 0:
            return {"status": f"Download successful: {process.stdout.strip()}"}
        else:
            return {"status": f"Error in download: {process.stderr.strip()}"}

    async def download_audio(self, parameters):
        return await self.download_with_ytdlp(parameters, "ba")

    async def download_video(self, parameters):
        return await self.download_with_ytdlp(parameters, "bv+ba/b")

    def vtt_to_text(self, vtt_path):
        with open(vtt_path, 'r', encoding='utf-8') as vtt_file:
            lines = vtt_file.readlines()
        captions = []
        previous_line = ""
        for line in lines:
            if line.strip() == '' or '-->' in line or line.startswith(('WEBVTT', 'NOTE')):
                continue
            caption = line.strip()
            caption = re.sub(r'<.*?>', '', caption)
            if caption != previous_line:
                captions.append(caption)
                previous_line = caption
        plain_text = ' '.join(captions)

        os.remove(vtt_path)
        return plain_text

    async def download_subtitles_and_convert(self, parameters):
        video_url = parameters.get("video_url")
        if not video_url:
            return {"status": "Error: No video_url was provided."}
        directory = self.downloads_directory
        vtt_path = os.path.join(directory, "video_transcript.en.vtt")
        yt_dlp_command = [
            self.yt_dlp_path, 
            "--write-auto-subs", "--write-subs", "--sub-lang", "en", "--skip-download",
            "-o", os.path.join(directory, "video_transcript.%(ext)s"),
            video_url
        ]
        process = subprocess.run(yt_dlp_command, capture_output=True, text=True, cwd=self.yt_dlp_directory)
        if process.returncode != 0:
            return {"status": f"Error in subtitle download: {process.stderr.strip()}"}
        
        plain_text = self.vtt_to_text(vtt_path)
        return {"status": f"Subtitle and conversion successful. plain_text: {plain_text}"}

    async def get_video_comments(self, parameters):
        video_url = parameters.get("video_url")
        if not video_url:
            return {"status": "Error: no video_url provided"}
        directory = self.downloads_directory
        comments_path = os.path.join(directory, "comments.json")
        yt_dlp_command = [
            self.yt_dlp_path,
            "--write-comments",
            "--print-to-file", "after_filter:%(comments)j",
            comments_path,
            "--parse-meta", "video::(?P<comments>)",
            "--skip-download",
            "--no-write-info-json",
            video_url
        ]
        process = subprocess.run(yt_dlp_command, capture_output=True, text=True, cwd=self.yt_dlp_directory)
        if process.returncode == 0:
            comments = []
            with open(comments_path, 'r') as file:
                comments_json = json.load(file)
            for comment in comments_json:
                author = comment.get('author')
                text = comment.get('text')
                likes = comment.get('like_count')
                comments.append({"author": author, "comment_text": text, "likes": likes})
            os.remove(comments_path)
            return {"status": "Comments retrieval successful", "comments": comments}
        else:
            return {"status": f"Error in comments retrieval: {process.stderr.strip()}"}

    async def execute_tool(self, tool_name, parameters):
        function_response = "There was a problem with performing this action for YouTube. Could not perform action."
        instant_response = ""
        
        if self.settings.debug_mode:
            self.start_execution_benchmark()
            await self.printr.print_async(f"Executing {tool_name} with parameters: {parameters}", color=LogType.INFO)
        
        if tool_name == "search_videos":
            function_response = await self.search_videos(parameters)

        elif tool_name in {"download_audio", "download_video"}:
            function_map = {
                "download_audio": self.download_audio,
                "download_video": self.download_video,
            }
            function_response = await function_map[tool_name](parameters)

        elif tool_name == "download_subtitles_and_convert":
            function_response = await self.download_subtitles_and_convert(parameters)
            
        elif tool_name == "get_video_comments":
            function_response = await self.get_video_comments(parameters)

        if self.settings.debug_mode:
            await self.printr.print_async(f"Function response: {function_response}", color=LogType.INFO)
            await self.print_execution_time()
        
        return function_response, instant_response