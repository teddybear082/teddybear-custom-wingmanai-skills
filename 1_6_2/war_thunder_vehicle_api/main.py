import os
import requests
import re
from typing import TYPE_CHECKING
from api.enums import LogType, WingmanInitializationErrorType
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from skills.skill_base import Skill
from services.file import get_writable_dir
from rapidfuzz import process, fuzz, utils
if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

API_BASE_URL = 'https://www.wtvehiclesapi.sgambe.serv00.net/api'

class WarThunderVehicleAPI(Skill):
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
                "get_vehicle_information",
                {
                    "type": "function",
                    "function": {
                        "name": "get_vehicle_information",
                        "description": "Get information about a War Thunder vehicle using the vehicle's identifier.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "identifier": {
                                    "type": "string",
                                    "description": "The identifier of the War Thunder vehicle.",
                                }
                            },
                            "required": ["identifier"],
                        },
                    },
                },
            ),
        ]
        return tools

    async def execute_tool(self, tool_name: str, parameters: dict[str, any]) -> tuple[str, str]:
        function_response = "Error: Could not retrieve vehicle information."
        instant_response = ""

        if tool_name == "get_vehicle_information":
            # the API seems only to like lower case
            identifier = parameters.get("identifier").lower()
            url = f"{API_BASE_URL}/vehicles/{identifier}"

            if self.settings.debug_mode:
                self.start_execution_benchmark()
                await self.printr.print_async(
                    f"Executing get_vehicle_information with URL: {url}",
                    color=LogType.INFO,
                )

            # Get information first from API
            response = requests.get(url, headers={"accept": "application/json"})

            if response.status_code == 200:
                function_response = response.json()
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"Vehicle found in API, details located: {response.json()}",
                        color=LogType.INFO,
                    )
            else:
                search_url = f"{API_BASE_URL}/vehicles/search/{identifier}"
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"Vehicle not found in API. Searching using URL: {search_url}",
                        color=LogType.INFO,
                    )
                search_response = requests.get(search_url, headers={"accept": "application/json"})
                if search_response.status_code == 200:
                    search_results = search_response.json()
                    if search_results:
                        # should we maybe check if there is more than one result, and if so, put all the results back to the user with a question which one to find more information on?
                        first_result = search_results[0]
                        identifier = first_result
                        final_url = f"{API_BASE_URL}/vehicles/{first_result}"
                        final_response = requests.get(final_url, headers={"accept": "application/json"})
                        if final_response.status_code == 200:
                            if self.settings.debug_mode:
                                await self.printr.print_async(
                                    f"Vehicle found in API with search, details located: {final_response.json()}",
                                    color=LogType.INFO,
                                )
                            function_response = final_response.json()
                        else:
                            function_response = f"Vehicle found in search in API but error in retrieving details using identifier {first_result}."
                    else:
                        function_response = "No vehicle matched the search query in API."
                else:
                    function_response = "Vehicle identifier search in API failed."

            # Now get information from text files
            data_directory = get_writable_dir("skills/war_thunder_vehicle_api/data/")
            # Open text file that starts with first letter of identifier
            file_content = ""
            file_name = f"{identifier[0].upper()}.txt"
            file_path = os.path.join(data_directory, file_name)
            with open(file_path, "r", encoding="utf-8") as file:
                file_content = file.read()
            models_in_file = self.extract_model_names(file_content)
            if models_in_file:
                matching_models = self.find_best_matches(identifier, models_in_file)
                final_model_info = ""
                for matching_model in matching_models:
                    model_info = self.extract_model_info(matching_model, file_content)
                    final_model_info += model_info
                if isinstance(function_response, dict):
                    # If function_response is a dictionary, add additional data as the value of a new key
                    function_response["Additional_info_from_text_files"] = final_model_info
                elif isinstance(function_response, str):
                    # If function_response is a string, concatenate additional data to it
                    function_response += f"Information from local text file database: {final_model_info}"
                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"Vehicle found in text file database, details located:\n\n{final_model_info}",
                        color=LogType.INFO,
                    )

            if self.settings.debug_mode:
                await self.print_execution_time()

        return function_response, instant_response

    # Functions used for extracting portions of file library for vehicles for War Thunder

    # Step 1: Extract the relevant model names from the data
    def extract_model_names(self, data):
        return re.findall(r"Model (\w+)", data)

    # Step 2: Fuzzy match the input with available model names
    def find_best_matches(self, query, model_names):
        best_matches = process.extract(query, model_names, scorer=fuzz.WRatio, limit=4, processor=utils.default_process)
        strings = [item[0] for item in best_matches]
        return strings if strings else None

    # Step 3: Extract relevant information for the matched model
    def extract_model_info(self, model_name, data):
        pattern = re.compile(rf"Model {model_name}.*?(?=Model|\Z)", re.S)
        match = pattern.search(data)
        return match.group(0) if match else "Model information not found."