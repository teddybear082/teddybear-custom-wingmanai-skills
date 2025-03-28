import traceback
import os
import time
import base64
from io import BytesIO
from datetime import datetime
import requests
from typing import TYPE_CHECKING, Optional, Tuple, Dict, List
from api.interface import SettingsConfig, SkillConfig, WingmanInitializationError
from services.benchmark import Benchmark
from api.enums import LogSource, LogType
from skills.skill_base import Skill
from services.file import get_writable_dir
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from markdownify import markdownify
from dom.service import DomService
from PIL import Image, ImageDraw, ImageFont

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

WAIT_TIME_BETWEEN_STEPS : float = 1.0

class BrowserUse(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        # Internal state variables, question of whether we should allow muliple browser sessions simulataneously?
        self.driver = None
        self.cached_selector_map = {}
        self.dom_service = None
        self.user_message_count = 0
        self.num_browser_tabs = 0
        self.images_cache = []
        self.reasoning_cache = []
        self.gif_prompt = ""
        self.last_assistant_message_for_gif = ""
        # User config variables
        self.chrome_browser_path = None # Attempt to use user's own browser executable
        self.chrome_user_data_path = None # Attempt to use user's own chrome user data to reduce logins
        self.chrome_remote_debugging_port = None # If user wants to enable remote debugging on their browser then we may be able to interact with it midstream, e.g. "I have my browser up, complete this form for me."
        self.use_vision = True # Whether to run a vision pass in getting next recommended steps
        self.use_headless_mode = False # Whether to physically display the chrome browser on the user's computer
        self.create_gifs = False # Whether to auto generate gifs after a browser use session
        # To set chrome to launch in remote debugging mode, create a shortcut and add this to the target: --remote-debugging-port=9222 --user-data-dir="C:\Users\YOURUSERNAME\AppData\Local\Google\Chrome\User Data\Default"

    async def validate(self) -> list[WingmanInitializationError]:
        errors = await super().validate()
        self.chrome_browser_path = self.retrieve_custom_property_value(
            "chrome_browser_path", errors
        )
        self.chrome_user_data_path = self.retrieve_custom_property_value(
            "chrome_user_data_path", errors
        )
        self.chrome_remote_debugging_port = self.retrieve_custom_property_value(
            "chrome_remote_debugging_port", errors
        )
        self.use_vision = self.retrieve_custom_property_value(
            "use_vision", errors
        )
        self.use_headless_mode = self.retrieve_custom_property_value(
            "use_headless_mode", errors
        )
        self.use_voice_feedback = self.retrieve_custom_property_value(
            "use_voice_feedback", errors
        )
        self.create_gifs= self.retrieve_custom_property_value(
            "create_gifs", errors
        )
        if self.chrome_browser_path and self.chrome_browser_path != " ":
            if not os.path.isfile(self.chrome_browser_path):
                self.chrome_browser_path = None
        if self.chrome_user_data_path and self.chrome_user_data_path != " ":
            if not os.path.isdir(self.chrome_user_data_path):
                self.chrome_user_data_path = None
            else:
                self.chrome_user_data_path = os.path.normpath(self.chrome_user_data_path)
        # Test debugging port here to see if it is open?
        return errors

    async def setup_browser(self):
        # If a driver is already running, close it as we're starting fresh
        if self.driver:
            self.driver.quit()
        
        # Clear previous caches
        self.cached_selector_map = {}
        self.dom_service = None
        self.num_browser_tabs = 0
        self.images_cache = []
        self.reasoning_cache = []
        
        # Set up new browser
        options = webdriver.ChromeOptions()
        if self.chrome_browser_path:
            options.binary_location = self.chrome_browser_path #"C:\Program Files\Google\Chrome\Application\chrome.exe"
            if self.settings.debug_mode:
                message = "Running browser use skill using user's own Chrome.exe file."
                await self.printr.print_async(text=message, color=LogType.INFO)
        if self.chrome_user_data_path:
            options.add_argument(f"--user-data-dir={self.chrome_user_data_path}") #"--user-data-dir=C:\\Users\\YOURUSERNAME\\AppData\\Local\\Google\\Chrome\\User Data\\Default")
            if self.settings.debug_mode:
                message = "Running browser use skill using user's own browser user data"
                await self.printr.print_async(text=message, color=LogType.INFO)
        if self.use_headless_mode:
            options.headless = True # This did not work, old way of doing it
            options.add_argument("--headless=new")
            if self.settings.debug_mode:
                message = "Running browser use skill in headless mode so browser window will not open."
                await self.printr.print_async(text=message, color=LogType.INFO)
        # Add same options to avoid bot detection as used in browser-use
        options.add_argument('--no-sandbox') # This works
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_argument('--disable-infobars') # This does not crash but doesn't eliminate the "controlled by automation" message it is supposed to
        options.add_argument('--disable-background-timer-throttling')
        options.add_argument('--disable-popup-blocking')
        options.add_argument('--disable-backgrounding-occluded-windows')
        options.add_argument('--disable-renderer-backgrounding')
        options.add_argument('--no-first-run')
        options.add_argument('--no-default-browser-check')
        #options.add_argument('--no-startup-window') # This does NOT work, causes chrome to freeze for some reason
        options.add_argument('--window-position=0,0')
        options.add_argument("--window-size=1920,1080")
        options.add_argument('--disable-web-security')
        options.add_argument('--disable-site-isolation-trials')
        options.add_argument('--disable-features=IsolateOrigins,site-per-process')
        # If remote debugging port is set make sure it is valid before using it
        if self.chrome_remote_debugging_port and self.chrome_remote_debugging_port != 0 and self.chrome_remote_debugging_port != "0":
            try:
                response = requests.get(f"http://127.0.0.1:{self.chrome_remote_debugging_port}/json", timeout=2)
                if response.status_code == 200:
                    options.add_argument(f'--remote-debugging-port={self.chrome_remote_debugging_port}')
                    if self.settings.debug_mode:
                        message = "Running browser use skill with remote debugging port so chrome must already be open."
                        await self.printr.print_async(text=message, color=LogType.INFO)
                else:
                    if self.settings.debug_mode:
                        message = f"Remote debugging port set to {self.chrome_remote_debugging_port} but cannot connect so using default mode of browser use."
                        await self.printr.print_async(text=message, color=LogType.INFO)
            except:
                if self.settings.debug_mode:
                    message = f"Remote debugging port set to {self.chrome_remote_debugging_port} cannot connect so using default mode of browser use."
                    await self.printr.print_async(text=message, color=LogType.INFO)
        self.driver = webdriver.Chrome(options=options)
        self.dom_service = DomService(self.driver)

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "open_web_page",
                {
                    "type": "function",
                    "function": {
                        "name": "open_web_page",
                        "description": "Opens a web page specified by the user.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "url": {
                                    "type": "string",
                                    "description": "The URL of the web page to open.",
                                },
                            },
                            "required": ["url"],
                        },
                    },
                },
            ),
            (
                "switch_active_browser_tab",
                {
                    "type": "function",
                    "function": {
                        "name": "switch_active_browser_tab",
                        "description": "Changes to a different browser tab based on the tab's name.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "target_browser_tab": {
                                    "type": "string",
                                    "description": "The identifier of the browser tab to change to, it will be a long string of letters and numbers.",
                                },
                            },
                            "required": ["target_browser_tab"],
                        },
                    },
                },
            ),
            (
                "get_recommended_action_from_browser_state",
                {
                    "type": "function",
                    "function": {
                        "name": "get_recommended_action_from_browser_state",
                        "description": "Examine the state of the browser and recommend the next action to accomplish the user's goal.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "user_goal": {
                                    "type": "string",
                                    "description": "The goal the user wants to accomplish.",
                                },
                            },
                            "required": ["user_goal"],
                        },
                    },
                },
            ),
            (
                "click_element",
                {
                    "type": "function",
                    "function": {
                        "name": "click_element",
                        "description": "Clicks a given clickable element by its Box_ID index.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the clickable element to click.",
                                },
                            },
                            "required": ["Box_ID"],
                        },
                    },
                },
            ),
            (
                "type_text_into_field",
                {
                    "type": "function",
                    "function": {
                        "name": "type_text_into_field",
                        "description": "Types text into a given field specified by its Box_ID index.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the text field element to type into.",
                                },
                                "text_to_type": {
                                    "type": "string",
                                    "description": "The text to type into the field.",
                                },
                            },
                            "required": ["Box_ID", "text_to_type"],
                        },
                    },
                },
            ),
            (
                "scroll_by_amount",
                {
                    "type": "function",
                    "function": {
                        "name": "scroll_by_amount",
                        "description": "Scrolls the browser window by a specified amount. Positive y_offset scrolls down, negative scrolls up.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "x_offset": {
                                    "type": "number",
                                    "description": "Horizontal offset to scroll by.",
                                },
                                "y_offset": {
                                    "type": "number",
                                    "description": "Vertical offset to scroll by.",
                                },
                            },
                            "required": ["x_offset", "y_offset"],
                        },
                    },
                },
            ),
            (
                "navigate_page",
                {
                    "type": "function",
                    "function": {
                        "name": "navigate_page",
                        "description": "Navigate the page. Accepts actions: back, forward, refresh, minimize, maximize, new_tab, and get_list_of_browser_tabs.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "description": "Navigation action: 'back', 'forward', 'refresh', 'minimize' 'maximize', 'new_tab', 'get_list_of_browser_tabs'",
                                },
                            },
                            "required": ["action"],
                        },
                    },
                },
            ),
            (
                "double_click_element",
                {
                    "type": "function",
                    "function": {
                        "name": "double_click_element",
                        "description": "Double clicks on an element specified by its Box_ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the element to double click.",
                                },
                            },
                            "required": ["Box_ID"],
                        },
                    },
                },
            ),
            (
                "right_click_element",
                {
                    "type": "function",
                    "function": {
                        "name": "right_click_element",
                        "description": "Right clicks on an element specified by its Box_ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the element to right click.",
                                },
                            },
                            "required": ["Box_ID"],
                        },
                    },
                },
            ),
            (
                "drag_and_drop",
                {
                    "type": "function",
                    "function": {
                        "name": "drag_and_drop",
                        "description": "Drags an element from a source Box_ID and drops it onto a target Box_ID.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "source_Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the element to drag.",
                                },
                                "target_Box_ID": {
                                    "type": "integer",
                                    "description": "The index for the highlighted box of the target element where the source will be dropped.",
                                },
                            },
                            "required": ["source_Box_ID", "target_Box_ID"],
                        },
                    },
                },
            ),
            (
                "close_browser",
                {
                    "type": "function",
                    "function": {
                        "name": "close_browser",
                        "description": "Closes the browser window and quits the driver.",
                    },
                },
            ),
            (
                "get_browser_window_text_content",
                {
                    "type": "function",
                    "function": {
                        "name": "get_browser_window_text_content",
                        "description": "Try to get the text content of a browser window or tab if available.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "user_query": {
                                    "type": "string",
                                    "description": "If applicable, the information the user wants.",
                                },
                            },
                        },
                    },
                },
            ),
        ]
        return tools

    async def execute_tool(self, tool_name: str, parameters: dict[str, any], benchmark: Benchmark) -> tuple[str, str]:
        function_response = "Error executing tool. Please try again."
        instant_response = ""
        benchmark.start_snapshot(f"BrowserUse: {tool_name}")
        if self.settings.debug_mode:
            message = f"BrowserUse: executing tool '{tool_name}' with params: {parameters}. Waiting one second in between actions."
            await self.printr.print_async(text=message, color=LogType.INFO)

        Box_ID = parameters.get("Box_ID")
        text_to_type = parameters.get("text_to_type")

        # Before calling browser use tool make sure our driver is set up, and then wait designated time before next action
        if tool_name != "close_browser":
            if not self.driver:
                await self.setup_browser()
            # If we have a driver make sure its still active by running a simple function
            else:
                try:
                    test = self.driver.title
                except:
                    await self.setup_browser()
            # Check whether a tab was opened since last action, if so, assume we want that tab to be active
            current_tab_count = len(self.driver.window_handles)
            if current_tab_count > self.num_browser_tabs:
                self.num_browser_tabs = current_tab_count
                self.driver.switch_to.window(self.driver.window_handles[-1])

            # Adding latency of second for all tool calls but closing the browser, this skill is intended for long running actions and this delay may help with overruns.
            time.sleep(WAIT_TIME_BETWEEN_STEPS)

        if tool_name == "open_web_page":
            url = parameters.get("url")
            self.driver.get(url)
            page_title = self.driver.title
            function_response = f"Opened web page: {url} with title: {page_title}."
                            

        elif tool_name == "click_element":
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            try:
                element_for_Box_ID = self.cached_selector_map[Box_ID]
                element_to_click = self.driver.find_element(By.XPATH, element_for_Box_ID.xpath)
                if element_to_click:
                    self.scroll_to_element(element_to_click)
                    self.click_element(element_to_click)
                    function_response = f"Clicked element."
                else:
                    function_response = f"Could not click element.  Could not locate element."
            except StaleElementReferenceException:
                function_response = "Could not click element, reference to element was stale."
            except Exception as e:
                function_response = f"There was an unexpected error in trying to click the element: {e}"

        elif tool_name == "type_text_into_field":
            try:
                element_for_Box_ID = self.cached_selector_map[Box_ID]
                element_to_type_into = self.driver.find_element(By.XPATH, element_for_Box_ID.xpath)
                if element_to_type_into:
                    self.scroll_to_element(element_to_type_into)
                    self.click_element(element_to_type_into)
                    #element_to_type_into.clear()
                    ActionChains(self.driver).send_keys(text_to_type).key_down(Keys.ENTER).key_up(Keys.ENTER).perform()
                    #time.sleep(0.3)
                    #element_to_type_into.send_keys(text_to_type)
                    #time.sleep(0.3)
                    #element_to_type_into.send_keys(Keys.ENTER)
                    function_response = f"Typed text {text_to_type} into element."
                else:
                    function_response = f"Could not type.  Can't seem to find element to type into."
            except StaleElementReferenceException:
                function_response = "Could not type into element, reference to element was stale."
            except Exception as e:
                function_response = f"There was an unexpected error in trying to type into the element: {e}"

        elif tool_name == "scroll_by_amount":
            # Scroll by x and y offsets
            self.driver.switch_to.window(self.driver.current_window_handle)
            x_offset = parameters.get("x_offset", 0)
            y_offset = parameters.get("y_offset", 0)
            self.driver.execute_script("window.scrollBy(arguments[0], arguments[1]);", x_offset, y_offset)
            function_response = f"Scrolled by x: {x_offset}, y: {y_offset}."

        elif tool_name == "navigate_page":
            action = parameters.get("action", "").lower()
            if action == "back":
                self.driver.back()
                function_response = "Navigated back."
            elif action == "forward":
                self.driver.forward()
                function_response = "Navigated forward."
            elif action == "refresh":
                self.driver.refresh()
                function_response = "Page refreshed."
            elif action == "minimize":
                self.driver.minimize_window()
                function_response = "Browser minimized."
            elif action == "maximize":
                self.driver.maximize_window()
                function_response = "Browser maximized."
            elif action == "new_tab":
                self.driver.switch_to.new_window()
                function_response = "New browser tab opened."
            elif action == "get_list_of_browser_tabs":
                current_tab = self.driver.current_window_handle
                browser_tabs = self.get_browser_tabs()
                function_response = f"List of current browser tabs: {browser_tabs}, the current active browser tab is: {current_tab}."
            else:
                function_response = f"Unknown navigation action: {action}."

        elif tool_name == "double_click_element":
            try:
                element_for_Box_ID = self.cached_selector_map[Box_ID]
                element = self.driver.find_element(By.XPATH, element_for_Box_ID.xpath)
                if element:
                    self.scroll_to_element(element)
                    action_chain = ActionChains(self.driver)
                    action_chain.double_click(element).perform()
                    function_response = "Double clicked element."
                else:
                    function_response = "Element not found for double click."
            except Exception as e:
                function_response = f"Error in double clicking element: {e}"

        elif tool_name == "right_click_element":
            try:
                element_for_Box_ID = self.cached_selector_map[Box_ID]
                element = self.driver.find_element(By.XPATH, element_for_Box_ID.xpath)
                if element:
                    self.scroll_to_element(element)
                    action_chain = ActionChains(self.driver)
                    action_chain.context_click(element).perform()
                    function_response = "Right clicked element."
                else:
                    function_response = "Element not found for right click."
            except Exception as e:
                function_response = f"Error in right clicking element: {e}"

        elif tool_name == "drag_and_drop":
            try:
                source_Box_ID = parameters.get("source_Box_ID")
                target_Box_ID = parameters.get("target_Box_ID")
                source_element = self.driver.find_element(By.XPATH, self.cached_selector_map[source_Box_ID].xpath)
                target_element = self.driver.find_element(By.XPATH, self.cached_selector_map[target_Box_ID].xpath)
                if source_element and target_element:
                    #self.scroll_to_element(source_element)
                    #self.scroll_to_element(target_element)
                    action_chain = ActionChains(self.driver)
                    action_chain.drag_and_drop(source_element, target_element).perform()
                    function_response = "Drag and drop performed successfully."
                else:
                    function_response = "Source or target element not found."
            except Exception as e:
                function_response = f"Error in drag and drop: {e}"

        elif tool_name == "close_browser":
            if self.driver:
                self.driver.quit()
                self.driver = None
                self.cached_selector_map = {}
                self.dom_service = None
                function_response = "Browser closed."
            else:
                function_response = "No browser instance to close."

        elif tool_name == "switch_active_browser_tab":
            target_browser_tab = parameters.get("target_browser_tab")
            try:
                self.driver.switch_to.window(target_browser_tab)
                function_response = f"Active tab switched to {target_browser_tab}"
            except Exception as e:
                function_response = f"There was a problem switching the active tab: {e}"

        elif tool_name == "get_browser_window_text_content":
            # Add audio feedback at longer steps,if enabled by the user
            if self.use_voice_feedback:
                announcement = "Getting text from web page."
                self.threaded_execution(self.wingman.play_to_user, announcement, True, self.wingman.config.sound)
            self.driver.switch_to.window(self.driver.current_window_handle)
            user_query = parameters.get("user_query")
            llm_response = None
            if self.use_vision:
                screenshot_base64 = self.driver.get_screenshot_as_base64()
                await self.printr.print_async(
                    "Analyzing screenshot of browser:",
                    color=LogType.INFO,
                    source=LogSource.WINGMAN,
                    source_name=self.wingman.name,
                    skill_name=self.name,
                    additional_data={"image_base64": screenshot_base64},
                )
                messages = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Summarize any text content you see in the browser screen. If applicable, pay particular attention to and try to provide information in response to the user's query which was: {user_query}"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{screenshot_base64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ]
                completion = await self.llm_call(messages)
                llm_response = (
                    completion.choices[0].message.content
                    if completion and completion.choices
                    else ""
                )
            try:
                #result = self.driver.execute_script("return document.body.textContent;")
                result = markdownify(self.driver.page_source)
                function_response = f"Attempted to obtain full text content of browser page. Result: {result}"
            except Exception as e:
                function_response = f"Error attempting to get text content of browser page: {e}"
            if llm_response:
                function_response += f"\n\n IMPORTANT: In addition, visual analysis of the browser window provided the following information: {llm_response}"

        elif tool_name == "get_recommended_action_from_browser_state":
            if self.use_voice_feedback:
                announcement = "Analyzing web page."
                self.threaded_execution(self.wingman.play_to_user, announcement, True, self.wingman.config.sound)
            self.driver.switch_to.window(self.driver.current_window_handle)
            user_goal = parameters.get("user_goal")
            interactable_elements = await self.get_clickable_elements(self.dom_service)
            #if self.settings.debug_mode:
                #await self.printr.print_async(f"Found interactable elements: {interactable_elements}", LogType.INFO)
            screenshot_base64 = self.driver.get_screenshot_as_base64()
            await self.printr.print_async(
                "Current state of browser:",
                color=LogType.INFO,
                source=LogSource.WINGMAN,
                source_name=self.wingman.name,
                skill_name=self.name,
                additional_data={"image_base64": screenshot_base64},
            )
            current_tab = self.driver.current_window_handle
            tab_dictionary_array = self.get_browser_tabs()
            self.driver.switch_to.window(current_tab)
            system_prompt = f"""
                You are a helpful computer control assistant and have access to the user's browser to perform actions in it.
                The current website the user is on is: {self.driver.current_url}.  The tabs in the browser are: {tab_dictionary_array} and the current browser tab is: {current_tab}.
                Your PRIME OBJECTIVE is to recommend the next browser step to accomplish the user's goal. Your blueprint to accomplish your PRIME OBJECTIVE is as follows:
                (1) Restate the user's goal, if any: {user_goal}.
                (2) EITHER provide details about the next interactable browser element that should be interacted with, starting with its Box ID (the number of the box its surrounded by) OR the other browser action that should be performed, for example:
                        (a) click / double click / right click the element at BOX_ID: (number), which is (brief explanation)
                        (b) open web page to URL: (url)
                        (c) get the list of available browser tabs so we can determine which we need next
                        (d) open a new browser tab and then go to URL: (url)
                        (e) switch browser tab to: (browser tab numeric identifier)
                        (f) refresh the page
                        (g) go back a page
                        (h) go forward a page
                        (i) scroll up / down / left / right by (units)
                        (j) type into the element at BOX_ID: (number) the following text: (text to type)
                        (k) drag from the element at BOX_ID: (number) and drop to the element at BOX_ID: (number)
                        (l) extract the text from the browser page to try to answer the user's question which is: (user question)
                (3) If the user's goal is already met, state that and provide any necessary details or information in response to the user's goal if the user wanted information.
                (4) If you hit a roadblock and cannot accomplish the user's goal, explain the problem and give a recommendation how an AI might try to fix the problem.
            """

            if self.use_vision:
                messages = [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Keeping in mind the user's goal '{user_goal}', which element should be acted upon next or which action should be performed? If none, and the user asked for information be sure to provide it.  Here are the interactable elements: {interactable_elements}.  \n\nIf it matters, the available browser tabs are: {self.driver.window_handles}, and the current active browser tab is: {self.driver.current_window_handle}"},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{screenshot_base64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ]
            # If not using vision, omit the image from the llm call
            else:
                messages = [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Keeping in mind the user's goal '{user_goal}', which element should be acted upon next? If none, and the user asked for information be sure to provide it.  Here are the interactable elements: {interactable_elements}.  \n\nIf it matters, the available browser tabs are: {self.driver.window_handles} and the current active browser tab is: {self.driver.current_window_handle}."},
                        ],
                    },
                ]
            completion = await self.llm_call(messages)
            llm_response = (
                completion.choices[0].message.content
                if completion and completion.choices
                else ""
            )
            function_response = f"The recommended action is: {llm_response}"
            
            # Handle gif tasks
            self.reasoning_cache.append(llm_response)
            self.images_cache.append(screenshot_base64)
            # Clear highlights before next action, and that way if there is no further action, highlights are gone.
            await self.remove_highlights()

        # Add some general browser info unless we closed the browser
        if tool_name != "close_browser":
            function_response += self.get_current_browser_state()
        benchmark.finish_snapshot()
        if self.settings.debug_mode:
            if tool_name != "get_recommended_action_from_browser_state":
                await self.printr.print_async(f"Full function response was: {function_response}", LogType.INFO)
            else:
                await self.printr.print_async("Got recommended action from browser state.", LogType.INFO)
        return function_response, instant_response

    # Not presently used
    def read_file(self, file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    # Not presently used
    def get_xpath(self, element):
        return element.xpath

    def scroll_to_element(self, element):
        self.driver.execute_script("arguments[0].scrollIntoView();", element)

    def click_element(self, element):
        self.driver.execute_script("arguments[0].click();", element)
    
    def get_browser_tabs(self):
        try:
            tab_dictionary_array = []
            current_tab = self.driver.current_window_handle
            for tab in self.driver.window_handles:
                self.driver.switch_to.window(tab)
                tab_dictionary = {f"{self.driver.title[:30]}": f"{tab}"}
                tab_dictionary_array.append(tab_dictionary)
            self.driver.switch_to.window(current_tab)
        except:
            tab_dictionary_array = []
        return tab_dictionary_array
    
    def get_current_browser_state(self):
        return f"""
        General browser info:
        List of current browser tabs: {self.get_browser_tabs()}
        Active browser tab: {self.driver.current_window_handle}
        Active browser URL: {self.driver.current_url}
        """

    async def get_clickable_elements(self, dom_service):
        # Make sure highlights are clear before getting new elements
        await self.remove_highlights()
        interactable_elements = {}
        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        dom_state = await dom_service.get_clickable_elements()
        selector_map = dom_state.selector_map
        dom_tree = dom_state.element_tree
        self.cached_selector_map = selector_map
        for key in selector_map.keys():
            interactable_elements[f"Box ID {key}"] = selector_map[key]
        return interactable_elements

    async def remove_highlights(self):
        try:
            self.driver.execute_script(HIGHLIGHT_REMOVAL_SCRIPT)
        except Exception as e:
            if self.settings.debug_mode:
                await self.printr.print_async(f'Failed to remove highlights: {str(e)}', LogType.INFO)
            pass

    # Clean up some potentially long and unnecessary context to increase speed
    async def on_add_user_message(self, message: str) -> None:
        # Generate GIF if it makes sense
        if self.images_cache and self.gif_prompt and self.create_gifs:
            # take a final screenshot to ensure we capture task completion
            screenshot = self.driver.get_screenshot_as_base64()
            self.images_cache.append(screenshot)
            created_gif_path = await self.create_gif(self.gif_prompt, self.images_cache, self.reasoning_cache)
            if self.settings.debug_mode:
                message = f"Gif created of task completed with BrowserUse skill.  The file is at: {created_gif_path}."
                await self.printr.print_async(text=message, color=LogType.INFO)
        # Cache prompt for possible gif future generation if other conditions met
        self.gif_prompt = message
        # Now perform context minimization steps
        self.user_message_count += 1

        # Check every five user messages
        if self.user_message_count % 5 == 0:
            # Check messages older than ten messages ago to ensure context was used
            for msg in self.wingman.messages[:-10]:
                # Some entries in messages will be ChatCompletions objects, not dictionaries, so skip those
                if isinstance(msg, dict):
                    if msg.get("role") == "tool" and "Attempted to obtain text content of browser page" in msg.get("content", ""):
                        msg.update({"content": "(Assistant provided the text content of the browser page.)"})
                        if self.settings.debug_mode:
                            await self.printr.print_async(f"Found and deleted an outdated text content of browser page message.", LogType.INFO)
                        # If we updated a message, reset user message count to ensure at least five user messages pass before another cleanup
                        self.user_message_count = 0

    async def on_add_assistant_message(self, message: str, tool_calls: list) -> None:
        self.last_assistant_message_for_gif = message

    # Helper function for creating optional .gif
    def create_text_slide(self, text, size, font_path="arial.ttf", initial_font_size=32, margin=10):
        """Create an image slide with centered text that auto-scales to fit using the given size."""
        img = Image.new("RGB", size, "black")
        draw = ImageDraw.Draw(img)
        # Wrap text into multiple lines (using your original word-splitting)
        def split_text(text, max_words=7):
            words = text.split()
            lines = []
            for i in range(0, len(words), max_words):
                line = ' '.join(words[i:i + max_words])
                lines.append(line)
            return "\n".join(lines)

        multiline_text = split_text(text)
        
        font_size = initial_font_size
        font = ImageFont.truetype(font_path, font_size)
        max_width = size[0] - 2 * margin
        max_height = size[1] - 2 * margin
        
        # Adjust font size until text fits within the area
        while True:
            bbox = draw.multiline_textbbox((0, 0), multiline_text, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]
            if text_width <= max_width and text_height <= max_height:
                break
            font_size -= 1
            if font_size <= 5:  # Prevent font size from becoming too small
                break
            font = ImageFont.truetype(font_path, font_size)
        
        # Center the text in the image
        x = (size[0] - text_width) / 2
        y = (size[1] - text_height) / 2
        
        draw.multiline_text((x, y), multiline_text, fill=(255, 255, 255), font=font, align="center")
        return img

    # Revised create_gif function that uses the helper above
    async def create_gif(self, prompt: str, images: list, reasoning_list: list) -> str:
        # Create the name for the GIF file
        date_str = datetime.now().strftime("%m%d%y")
        gif_name = f"BrowserUse_{date_str}_{'_'.join(prompt.split()[:3])}.gif"
        directory_path = get_writable_dir("files")
        gif_path = os.path.join(directory_path, gif_name)
        
        # Determine common slide size from the first image, capped at 1280x720
        if images:
            first_image_data = base64.b64decode(images[0])
            first_img = Image.open(BytesIO(first_image_data))
            first_width, first_height = first_img.size
            aspect_ratio = first_width / first_height
            max_width, max_height = 1280, 720
            if aspect_ratio > (max_width / max_height):
                target_width = max_width
                target_height = int(max_width / aspect_ratio)
            else:
                target_height = max_height
                target_width = int(max_height * aspect_ratio)
            common_size = (target_width, target_height)
        else:
            common_size = (640, 360)  # fallback if no images provided

        image_list = []
        # Create the initial slide with the prompt using common_size
        prompt_slide = self.create_text_slide("User Request: " + prompt, size=common_size)
        image_list.append(prompt_slide)
        
        # Process each image and corresponding reasoning text slide
        for i, img_b64 in enumerate(images):
            # Decode and prepare the image slide
            image_data = base64.b64decode(img_b64)
            img = Image.open(BytesIO(image_data))
            # Resize image to fit within the common slide size while maintaining aspect ratio
            img.thumbnail(common_size)
            image_list.append(img)
            
            # If there's a corresponding reasoning, add a text slide for it using common_size
            if i < len(reasoning_list):
                reasoning_slide = self.create_text_slide(reasoning_list[i], size=common_size)
                image_list.append(reasoning_slide)
        
        # Finally, add the assistant message slide using common_size
        assistant_slide = self.create_text_slide(self.last_assistant_message_for_gif, size=common_size)
        image_list.append(assistant_slide)
        
        # Save the frames as a GIF
        image_list[0].save(gif_path, save_all=True, append_images=image_list[1:], loop=0, duration=6000, disposal=2)
        
        # Clear caches
        self.images_cache = []
        self.reasoning_cache = []
        self.gif_prompt = ""
        return gif_path


    async def unload(self) -> None:
        """Unload the skill."""
        # If using headless mode, close any open browser instance as otherwise user will not know its still running
        if self.use_headless_mode:
            if self.driver:
                self.driver.quit()
                self.driver = None
        self.cached_selector_map = {}
        self.dom_service = None
        self.user_message_count = 0
        self.num_browser_tabs = 0
        self.images_cache = []
        self.reasoning_cache = []
        self.gif_prompt = ""

HIGHLIGHT_REMOVAL_SCRIPT = """
(function removeHighlights() {
  const container = document.getElementById("selenium-highlight-container");
  if (container) {
    container.parentNode.removeChild(container);
  }
})();
"""
