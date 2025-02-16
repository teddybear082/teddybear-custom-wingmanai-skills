import os
import datetime
from typing import TYPE_CHECKING
from api.enums import LogType
from api.interface import SettingsConfig, SkillConfig
from skills.skill_base import Skill
from services.file import get_writable_dir
from services.benchmark import Benchmark

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

class ConversationLogger(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.logging_enabled = False
        self.log_file_path = ""
        self.user_name =  ""

    async def validate(self):
        errors = await super().validate()
        self.logging_enabled = self.retrieve_custom_property_value("logging_enabled", errors)
        self.user_name = self.retrieve_custom_property_value("user_name", errors)
        self.initialize_logger()
        return errors

    def initialize_logger(self):
        date_str = datetime.datetime.now().strftime("%Y_%m_%d")
        self.log_file_path = f"{self.wingman.name}_conversation_{date_str}.log"
        log_dir = get_writable_dir(os.path.join("logs"))
        os.makedirs(log_dir, exist_ok=True)
        self.log_file_path = os.path.join(log_dir, self.log_file_path)

    async def on_add_user_message(self, message: str) -> None:
        success = False
        if self.logging_enabled:
            success = self.log_message(self.user_name + ":", message)
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Logged user message: {message}." if success else "There was an error logging the last user message",
                    color=LogType.INFO,
                )

    async def on_add_assistant_message(self, message: str, tool_calls: list) -> None:
        success = False
        if self.logging_enabled:
            if message:
                success = self.log_message(self.wingman.name + ":", message)
            if tool_calls:
                success = self.log_message(self.wingman.name, f"called the following tools: {tool_calls}.")
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Logged assistant message: {message} with tool calls: {tool_calls}." if success else "There was an error logging last assistant message or tool calls.",
                    color=LogType.INFO,
                )

    def log_message(self, sender: str, message: str) -> bool:
        try:
            with open(self.log_file_path, 'a') as log_file:
                if message:
                    timestamp = datetime.datetime.now().strftime("[%Y-%m-%d] (%H:%M)")
                    log_file.write(f"{timestamp} {sender} {message}\n")
                    return True
        except Exception as e:
            if self.settings.debug_mode:
                print(f"Logging error: {e}")
            return False
