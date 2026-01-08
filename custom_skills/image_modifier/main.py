from PIL import Image
import os
from typing import TYPE_CHECKING
from api.enums import LogType
from services.benchmark import Benchmark
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman


class ImageModifier(Skill):

    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "convert_image_format",
                {
                    "type": "function",
                    "function": {
                        "name": "convert_image_format",
                        "description": "Convert an image from one format to another.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "input_path": {
                                    "type": "string",
                                    "description": "Path to the input image file.",
                                },
                                "output_path": {
                                    "type": "string",
                                    "description": "Path to save the converted image file.",
                                },
                                "output_format": {
                                    "type": "string",
                                    "description": "Format to convert the image to (e.g., PNG, JPEG).",
                                },
                            },
                            "required": ["input_path", "output_path", "output_format"],
                        },
                    },
                },
            ),
            (
                "resize_image",
                {
                    "type": "function",
                    "function": {
                        "name": "resize_image",
                        "description": "Resize an image to the specified dimensions.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "input_path": {
                                    "type": "string",
                                    "description": "Path to the input image file.",
                                },
                                "output_path": {
                                    "type": "string",
                                    "description": "Path to save the resized image file.",
                                },
                                "width": {
                                    "type": "integer",
                                    "description": "The width to resize the image to.",
                                },
                                "height": {
                                    "type": "integer",
                                    "description": "The height to resize the image to.",
                                },
                            },
                            "required": ["input_path", "output_path", "width", "height"],
                        },
                    },
                },
            ),
            (
                "rename_image",
                {
                    "type": "function",
                    "function": {
                        "name": "rename_image",
                        "description": "Rename an image file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "current_path": {
                                    "type": "string",
                                    "description": "Path to the current image file.",
                                },
                                "new_path": {
                                    "type": "string",
                                    "description": "New path (including new filename) for the image.",
                                },
                            },
                            "required": ["current_path", "new_path"],
                        },
                    },
                },
            ),
        ]
        return tools

    async def execute_tool(
        self, tool_name: str, parameters: dict[str, any], benchmark: Benchmark
    ) -> tuple[str, str]:
        function_response = "Error in execution. Please try again."
        instant_response = ""

        benchmark.start_snapshot(f"Image Modifier: {tool_name}")

        if tool_name == "convert_image_format":
            input_path = parameters.get("input_path")
            output_path = parameters.get("output_path")
            output_format = parameters.get("output_format")

            try:
                image = Image.open(input_path)
                image.save(output_path, output_format)

                function_response = f"Image converted to {output_format} and saved to {output_path}."
            except Exception as e:
                function_response = str(e)

        elif tool_name == "resize_image":
            input_path = parameters.get("input_path")
            output_path = parameters.get("output_path")
            width = parameters.get("width")
            height = parameters.get("height")

            try:
                image = Image.open(input_path)
                resized_image = image.resize((width, height))
                resized_image.save(output_path)

                function_response = f"Image resized to {width}x{height} and saved to {output_path}."
            except Exception as e:
                function_response = str(e)

        elif tool_name == "rename_image":
            current_path = parameters.get("current_path")
            new_path = parameters.get("new_path")

            try:
                os.rename(current_path, new_path)
                function_response = f"Image file renamed to {new_path}."
            except Exception as e:
                function_response = str(e)

        if self.settings.debug_mode:
            await self.printr.print_async(
                f"Output produced by {tool_name} function: {function_response}",
                color=LogType.INFO,
            )

        benchmark.finish_snapshot()
        return function_response, instant_response
