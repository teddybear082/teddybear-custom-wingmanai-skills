from googlesearch import search
from api.enums import LogType
from api.interface import SettingsConfig, SkillConfig
from services.benchmark import Benchmark
from skills.skill_base import Skill
from trafilatura import fetch_url, extract
from trafilatura.settings import DEFAULT_CONFIG
from copy import deepcopy

# Copy default config file that comes with trafilatura
trafilatura_config = deepcopy(DEFAULT_CONFIG)
# Change download and max redirects default in config
trafilatura_config["DEFAULT"]["DOWNLOAD_TIMEOUT"] = "10"
trafilatura_config["DEFAULT"]["MAX_REDIRECTS "] = "3"

class GoogleSearch(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        
    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "perform_google_search",
                {
                    "type": "function",
                    "function": {
                        "name": "perform_google_search",
                        "description": "Performs a Google search with specified options.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "query": {
                                    "type": "string",
                                    "description": "The search query to execute.",
                                },
                                "lang": {
                                    "type": "string",
                                    "description": "Language code for the search results.",
                                },
                                "region": {
                                    "type": "string",
                                    "description": "Country code for region-specific results.",
                                },
                            },
                            "required": ["query"],
                        },
                    },
                },
            ),
        ]
        return tools

    async def execute_tool(self, tool_name: str, parameters: dict, benchmark: Benchmark) -> tuple:
        function_response = ""
        instant_response = ""
        if tool_name == "perform_google_search":
            benchmark.start_snapshot(f"GoogleSearchSkill: {tool_name}")
            query = parameters["query"]
            num_results = 3
            unique = True
            lang = parameters.get("lang", "en")
            region = parameters.get("region", None)
            if self.settings.debug_mode:
                message = f"GoogleSearchSkill: executing tool '{tool_name}' with query '{query}'"
                await self.printr.print_async(text=message, color=LogType.INFO)
            results = list(search(query, num_results=num_results, unique=unique, lang=lang, region=region))
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"googlesearch skill found results: {results}",
                    color=LogType.INFO,
                )
            processed_results = []

            async def gather_information(result):
                link = result
                # If a link is in search results or identified by the user, then use trafilatura to download its content and extract the content to text
                if link:
                    trafilatura_url = link
                    trafilatura_downloaded = fetch_url(
                        trafilatura_url, config=trafilatura_config
                    )
                    if self.settings.debug_mode:
                        await self.printr.print_async(
                            f"googlesearch skill analyzing website at: {link} for full content using trafilatura.",
                            color=LogType.INFO,
                        )
                    trafilatura_result = extract(
                        trafilatura_downloaded,
                        include_comments=False,
                        include_tables=False,
                    )
                    if trafilatura_result:
                        processed_results.append(
                            "website: "
                            + link
                            + "\n"
                            +  "content: "
                            + trafilatura_result
                        )
                        if self.settings.debug_mode:
                            await self.printr.print_async(
                                f"Trafilatura result for {link}: {trafilatura_result}.",
                                color=LogType.INFO,
                            )

                    else:
                        if self.settings.debug_mode:
                            await self.printr.print_async(
                                f"google_search skill could not extract results from website at: {link} for full content using trafilatura",
                                color=LogType.INFO,
                            )
                        processed_results.append("website: " + link + "\n" + "content: None able to be extracted")
            for result in results:
                self.threaded_execution(gather_information, result)

            final_results = "\n\n".join(processed_results)
            function_response = f"Results for '{query}': {final_results}"
            benchmark.finish_snapshot()
        return function_response, instant_response