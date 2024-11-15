import sqlite3
from datetime import datetime
from typing import TYPE_CHECKING
from os import path
from rapidfuzz import process, fuzz
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from api.enums import LogType
from skills.skill_base import Skill
from services.file import get_writable_dir
if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman
class ConversationMemory(Skill):
    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        db_directory = get_writable_dir(path.join("skills", "conversation_memory"))
        self.db_path = path.join(db_directory, "conversation_memory.db")
        self.create_db()

    def create_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS memories
                     (id INTEGER PRIMARY KEY, wingman_name TEXT, date TEXT, summary TEXT, tags TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS tags
                     (id INTEGER PRIMARY KEY, wingman_name TEXT, tag TEXT)''')
        conn.commit()
        conn.close()

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "store_conversation_summary",
                {
                    "type": "function",
                    "function": {
                        "name": "store_conversation_summary",
                        "description": "Stores a summary of the conversation with the wingman.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "summary": {
                                    "type": "string",
                                    "description": "Summary of the conversation.",
                                },
                                "tags": {
                                    "type": "string",
                                    "description": "Comma-separated tags related to the conversation topics.",
                                },
                            },
                            "required": ["summary", "tags"],
                        },
                    },
                },
            ),
            (
                "retrieve_conversation_memories",
                {
                    "type": "function",
                    "function": {
                        "name": "retrieve_conversation_memories",
                        "description": "Retrieves conversation summaries based on filters.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "start_date": {
                                    "type": "string",
                                    "description": "Start date for filtering memories (YYYY-MM-DD).",
                                },
                                "end_date": {
                                    "type": "string",
                                    "description": "End date for filtering memories (YYYY-MM-DD).",
                                },
                                "tags": {
                                    "type": "string",
                                    "description": "Comma-separated tags for filtering memories.",
                                },
                                "custom_query": {
                                    "type": "string",
                                    "description": "Custom SQL query string for filtering memories. Overwrites other filters.",
                                },
                            },
                        },
                    },
                },
            ),
            (
                "delete_conversation_memory",
                {
                    "type": "function",
                    "function": {
                        "name": "delete_conversation_memory",
                        "description": "Deletes a memory by its ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "id": {
                                    "type": "integer",
                                    "description": "ID of the memory to delete.",
                                },
                            },
                            "required": ["id"],
                        },
                    },
                },
            ),
            (
                "get_all_tags",
                {
                    "type": "function",
                    "function": {
                        "name": "get_all_tags",
                        "description": "Returns all tags used in the database.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            ),
            (
                "get_all_dates",
                {
                    "type": "function",
                    "function": {
                        "name": "get_all_dates",
                        "description": "Returns all unique dates present in the conversation memories.",
                        "parameters": {
                            "type": "object",
                            "properties": {},
                        },
                    },
                },
            ),
        ]
        return tools

    async def execute_tool(
        self, tool_name: str, parameters: dict[str, any]
    ) -> tuple[str, str]:
        function_response = "Error in processing. Can you please try your command again?"
        if tool_name == "store_conversation_summary":
            summary = parameters.get("summary")
            tags = parameters.get("tags").split(',')
            tags = [tag.strip() for tag in tags]
            tags = self.fuzzy_match_tags(tags)
            self.store_summary(summary, tags)
            function_response = f"Conversation summary stored successfully, with tags: {tags}."

        elif tool_name == "retrieve_conversation_memories":
            wingman_name = self.wingman.name
            start_date = parameters.get("start_date")
            end_date = parameters.get("end_date")
            tags = parameters.get("tags")
            custom_query = parameters.get("custom_query")
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Searching for memory of {wingman_name} with tags: {tags} and dates: {start_date} - {end_date}.",
                    color=LogType.INFO,
                )
            results = self.retrieve_memories(wingman_name, start_date, end_date, tags, custom_query)
            function_response = f"Retrieved memories (id, date, memory content): \n\n {results}"

        elif tool_name == "delete_conversation_memory":
            mem_id = parameters.get("id")
            self.delete_memory(mem_id)
            function_response = f"Memory with ID {mem_id} deleted successfully."
        elif tool_name == "get_all_tags":
            tags = self.get_all_tags()
            function_response = "Tags used for existing memories: " + ", ".join(tags)
        elif tool_name == "get_all_dates":
            dates = self.get_all_dates()
            function_response = "Dates found for existing memories: " + ", ".join(dates)
        if self.settings.debug_mode:
            await self.printr.print_async(
                f"Executed {tool_name}, response: {function_response}.",
                color=LogType.INFO,
            )
        return function_response, ""

    def store_summary(self, summary: str, tags: list) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        joined_tags = ",".join(tags)
        c.execute("INSERT INTO memories (wingman_name, date, summary, tags) VALUES (?, ?, ?, ?)",
                  (self.wingman.name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), summary, joined_tags))
        conn.commit()
        conn.close()
        self.store_tags(tags)

    def store_tags(self, tags: list) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        for tag in tags:
            if not self.tag_exists(tag, c):
                c.execute("INSERT INTO tags (wingman_name, tag) VALUES (?, ?)", (self.wingman.name, tag))
        conn.commit()
        conn.close()

    def tag_exists(self, tag: str, cursor) -> bool:
        cursor.execute("SELECT id FROM tags WHERE wingman_name = ? AND tag = ?", (self.wingman.name, tag))
        return cursor.fetchone() is not None

    def fuzzy_match_tags(self, tags: list) -> list:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT tag FROM tags WHERE wingman_name = ?", (self.wingman.name,))
        existing_tags = [row[0] for row in c.fetchall()]
        matched_tags = []
        for tag in tags:
            if existing_tags:
                matches = process.extract(tag, existing_tags, limit=3, scorer=fuzz.ratio)
                if matches and matches[0][1] > 60:
                    matched_tags.append(matches[0][0])
                else:
                    matched_tags.append(tag)
            else:
                matched_tags.append(tag)
        conn.close()
        return matched_tags

    def retrieve_memories(self, wingman_name: str = None, start_date: str = None, end_date: str = None, tags: str = None, custom_query: str = None) -> list:
        if custom_query:
            query = custom_query
            params = []
        else:
            query = "SELECT id, date, summary FROM memories WHERE wingman_name = ?"
            params = [wingman_name]
            if start_date:
                query += " AND DATE(date) >= ?"
                params.append(start_date)
            if end_date:
                query += " AND DATE(date) <= ?"
                params.append(end_date)
            if tags:
                tag_list = tags.split(',')
                tag_list = self.fuzzy_match_tags(tag_list)
                tag_conditions = " OR ".join(["tags LIKE ?"] * len(tag_list))
                query += f" AND ({tag_conditions})"
                params.extend([f"%{tag.strip()}%" for tag in tag_list])
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(query, params)
        results = [(row[0], row[1], row[2]) for row in c.fetchall()]
        conn.close()
        return results

    def delete_memory(self, mem_id: int) -> None:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        conn.commit()
        conn.close()

    def get_all_tags(self) -> list:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT DISTINCT tag FROM tags WHERE wingman_name = ?", (self.wingman.name,))
        tags = [row[0] for row in c.fetchall()]
        conn.close()
        return tags

    def get_all_dates(self) -> list:
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT DISTINCT date FROM memories WHERE wingman_name = ?", (self.wingman.name,))
        dates = [row[0] for row in c.fetchall()]
        conn.close()
        return dates