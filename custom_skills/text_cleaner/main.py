import re
from typing import TYPE_CHECKING
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from api.enums import LogType
from services.benchmark import Benchmark
from skills.skill_base import Skill
if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman
    
class TextCleaner(Skill):
    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.custom_phrases_to_remove = []
        self.remove_emojis = True
        self.remove_narration = False
        self.message_to_clean = ""
        
    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        custom_phrases_string = self.retrieve_custom_property_value("phrases_to_remove", errors)
        # Create a list of strings from comma-separated list
        self.custom_phrases_to_remove = [phrase.strip() for phrase in custom_phrases_string.split(",")]
        self.remove_emojis = self.retrieve_custom_property_value("remove_emojis", errors)
        self.remove_narration = self.retrieve_custom_property_value("remove_narration", errors)
        return errors

    async def on_play_to_user(self, text: str, sound_config: dict) -> str:
        """Intercept and clean the text before text-to-speech."""
        # 'text' will have assistant message but stripped of any markdown
        cleaned_text = text
        
        # Helper function to remove emojis (matched using Unicode emoji patterns)
        def remove_emojis(input_text):
            # Use a emoji regex pattern
            regex_pattern = r"[\U00010000-\U0010FFFF]"
            return re.sub(regex_pattern, "", input_text)

        # Helper function to remove portions surrounded by asterisks, unless they are just one word (assuming one word italics is a word emphasized rather than narration)
        def remove_narration(input_text):
            regex_pattern = r"\*(\S+\s+\S+.*?)\*"
            # Find the phrases in the original assistant message still containing the markdown
            removed_phrases = re.findall(regex_pattern, self.message_to_clean)
            # Remove them from the markdown-free text passed in the text variable of on_play_to_user
            narration_removed_text = input_text
            for phrase in removed_phrases:
                narration_removed_text = narration_removed_text.replace(phrase, "")
            return narration_removed_text
        
        # Remove narration (portions surrounded by asterisks and containing more than one word)
        if self.remove_narration:
            cleaned_text = remove_narration(cleaned_text)
       
       # Remove emojis
        if self.remove_emojis:
            cleaned_text = remove_emojis(cleaned_text)

        # Remove custom phrases, if any, using word boundaries and case-insensitivity
        for phrase in self.custom_phrases_to_remove:
            cleaned_text = re.sub(
                rf"\b{re.escape(phrase)}\b", 
                "", 
                cleaned_text, 
                flags=re.IGNORECASE
            )
            
        # Remove extra spaces created during cleanup
        cleaned_text = re.sub(r"\s+", " ", cleaned_text).strip()
        if self.settings.debug_mode:
            await self.printr.print_async(
                text=f"TextCleaner: Processed text '{text}' into '{cleaned_text}'",
                color=LogType.INFO
            )
        return cleaned_text
        
    async def on_add_assistant_message(self, message: str, tool_calls: list) -> None:
        # self.message_to_clean will have full markdown from the LLM if applicable, enabling us to find the text to remove between asterisks
        self.message_to_clean = message