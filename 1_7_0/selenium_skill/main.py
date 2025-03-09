import os
import time
from typing import TYPE_CHECKING, Optional, Tuple, Dict, List
from api.interface import SettingsConfig, SkillConfig
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

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

class SeleniumSkill(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.driver = None
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
                "get_recommended_action_from_browser_state",
                {
                    "type": "function",
                    "function": {
                        "name": "get_recommended_action_from_browser_state",
                        "description": "Examine the state of the browser and obtain an expert recommendation on the next step to take to accomplish the user's goal.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "user_goal": {
                                    "type": "string",
                                    "description": "The goal the user wants to accomplish.",
                                },
                            },
                            "required": ["url"],
                        },
                    },
                },
            ),
            (
                "get_clickable_elements",
                {
                    "type": "function",
                    "function": {
                        "name": "get_clickable_elements",
                        "description": "Returns all clickable elements on the current web page.",
                    },
                },
            ),
            (
                "click_element",
                {
                    "type": "function",
                    "function": {
                        "name": "click_element",
                        "description": "Clicks a given clickable element by index, unique ID or XPATH with unique ID being preferred if available, followed by index and then XPATH as a last resort.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "index": {
                                    "type": "integer",
                                    "description": "The index of the clickable element to click.",
                                },
                                "unique_ID": {
                                    "type": "string",
                                    "description": "The unique element ID of the clickable element to click.",
                                },
                                "XPATH": {
                                    "type": "string",
                                    "description": "The XPATH of the clickable element to click.",
                                },
                            },
                            "required": [],
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
                        "description": "Types text into a given field specified by the element index, unique ID or XPATH with unique ID being preferred if available, followed by index and then XPATH as a last resort..",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "index": {
                                    "type": "integer",
                                    "description": "The index of the text field element to type into.",
                                },
                                "unique_ID": {
                                    "type": "string",
                                    "description": "The unique element ID of the element to type into.",
                                },
                                "XPATH": {
                                    "type": "string",
                                    "description": "The XPATH of the clickable element to type into.",
                                },
                                "text_to_type": {
                                    "type": "string",
                                    "description": "The text to type into the field.",
                                },
                            },
                            "required": ["text_to_type"],
                        },
                    },
                },
            ),
            (
                "take_screenshot",
                {
                    "type": "function",
                    "function": {
                        "name": "take_screenshot",
                        "description": "Takes a screenshot of the current browser page.",
                    },
                },
            ),
        ]
        return tools
    async def execute_tool(self, tool_name: str, parameters: dict[str, any], benchmark: Benchmark) -> tuple[str, str]:
        function_response = "Error executing tool. Please try again."
        instant_response = ""
        benchmark.start_snapshot(f"SeleniumSkill: {tool_name}")
        if self.settings.debug_mode:
            message = f"SeleniumSkill: executing tool '{tool_name}' with params: {parameters}"
            await self.printr.print_async(text=message, color=LogType.INFO)

        index = parameters.get("index")
        unique_ID = parameters.get("unique_ID")
        xpath = parameters.get("XPATH")
        text_to_type = parameters.get("text_to_type")

        if tool_name == "open_web_page":
            if self.driver:
                self.driver.quit()
            url = parameters.get("url")
            options = webdriver.ChromeOptions()
            # options.headless = True
            self.driver = webdriver.Chrome(options=options)
            self.driver.get(url)
            page_title = self.driver.title
            function_response = f"Opened web page: {url} with title: {page_title}."


        elif tool_name == "get_clickable_elements":
            interactable_elements = self.get_clickable_elements()
            function_response = f"Interactable_elements are: {interactable_elements}."

        elif tool_name == "click_element":
            WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            all_elements = self.driver.find_elements(By.XPATH, "//*")
            try:
                if unique_ID:
                    element_to_click = self.driver.find_element(By.ID, unique_ID)
                    if element_to_click:
                        self.scroll_to_element(element_to_click)
                        self.click_element(element_to_click)
                        function_response = f"Clicked element {element_to_click.tag_name} with unique ID: {unique_ID}"
                
                elif index and (0 <= index < len(all_elements)):
                    element_to_click = all_elements[index]
                    self.scroll_to_element(element_to_click)
                    self.click_element(element_to_click)
                    function_response = f"Clicked element {element_to_click.tag_name} at index: {index}."
                
                elif xpath:
                    element_to_click = self.driver.find_element(By.XPATH, xpath)
                    if element_to_click:
                        self.scroll_to_element(element_to_click)
                        self.click_element(element_to_click)
                        function_response = f"Clicked element {element_to_click.tag_name} at xpath: {xpath}"
                else:
                    function_response = f"Could not click element."
            except StaleElementReferenceException:
                function_response = "Could not click element, reference to element was stale."

        elif tool_name == "type_text_into_field":
            all_elements = self.driver.find_elements(By.XPATH, "//*")
            try:
                if unique_ID:
                    element_to_type_into = self.driver.find_element(By.ID, unique_ID)
                    if element_to_type_into:
                        self.scroll_to_element(element_to_type_into)
                        self.click_element(element_to_type_into)
                        element_to_type_into.clear()
                        element_to_type_into.send_keys(text_to_type)
                        element_to_type_into.send_keys(Keys.ENTER)
                        function_response = f"Typed text {text_to_type} into element with unique_ID: {unique_ID}."
                elif index and (0 <= index < len(all_elements)):
                    element_to_type_into = all_elements[index]
                    if element_to_type_into and element_to_type_into.tag_name.lower() in ["input", "textarea"]:
                        self.scroll_to_element(element_to_type_into)
                        self.click_element(element_to_type_into)
                        element_to_type_into.clear()
                        element_to_type_into.send_keys(text_to_type)
                        element_to_type_into.send_keys(Keys.ENTER)
                        function_response = f"Typed text {text_to_type} into element at index: {index}."
                    else:
                        function_response = f"Element at index {index} is not a text entry field."
                elif xpath:
                    element_to_type_into = self.driver.find_element(By.XPATH, xpath)
                    if element_to_type_into:
                        self.scroll_to_element(element_to_type_into)
                        self.click_element(element_to_type_into)
                        element_to_type_into.clear()
                        element_to_type_into.send_keys(text_to_type)
                        element_to_type_into.send_keys(Keys.ENTER)
                        function_response = f"Typed text {text_to_type} into element with xpath: {xpath}."
                else:
                    function_response = f"Can't seem to find element to type into."
            except StaleElementReferenceException:
                function_response = "Could not type into element, reference to element was stale."

        elif tool_name == "take_screenshot":
            #screenshot_path = os.path.join(get_writable_dir(), 'files/screenshot.png')
            #self.driver.save_screenshot(screenshot_path)
            #function_response = f"Screenshot taken and saved to {screenshot_path}."
            screenshot_base64 = self.driver.get_screenshot_as_base64()
            await self.printr.print_async(
                    "Current state of browser:",
                    color=LogType.INFO,
                    source=LogSource.WINGMAN,
                    source_name=self.wingman.name,
                    skill_name=self.name,
                    additional_data={"image_base64": screenshot_base64},
                )
            function_response = "Screenshot was taken and shown to the user."

        elif tool_name == "get_recommended_action_from_browser_state":
            user_goal = parameters.get("user_goal")
            interactable_elements = self.get_clickable_elements()
            screenshot_base64 = self.driver.get_screenshot_as_base64()
            await self.printr.print_async(
                    "Current state of browser:",
                    color=LogType.INFO,
                    source=LogSource.WINGMAN,
                    source_name=self.wingman.name,
                    skill_name=self.name,
                    additional_data={"image_base64": screenshot_base64},
                )

            system_prompt = """
                You are a helpful computer control assistant and have access to the user's browser to perform actions in it.
                Your role is to recommend the nexr step to accomplosh the user's goal.
                You should provide all details you can about the next interactable elememt that should be interacted with and what should be done.  
                Or if you believe the user's goal has already been accomplished based in what you see, say that and provide any necessary information to the user.
                For example, if the user wanted information make sure to provide that information.
            """

            messages = [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"Keeping the user's ultimate goal in mind, which the user said was '{user_goal}', determine the next action to take on which screen element in the attached picture.  A list of interactable items is as follows: {interactable_elements}"},
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
            function_response = f"The recommended action is: {llm_response}"

        benchmark.finish_snapshot()
        if self.settings.debug_mode:
            await self.printr.print_async(f"Full function response was: {function_response}", LogType.INFO)
        return function_response, instant_response

    def read_file(self, file_path):
        with open(file_path, "r", encoding="utf-8") as file:
            return file.read()

    def is_interactable(self, element):
        tag_name = element.tag_name.lower()
        interactive_tags = {
            'a', 'button', 'input', 'textarea', 'select', 'details', 'summary',
            'dialog', 'video', 'audio', 'embed', 'object'
        }
        # Check if the element has an interactive ARIA role
        interactive_roles = {
            "button", "menuitem", "tab", "checkbox", "radio",
            "link", "combobox", "textbox", "progressbar", "slider", "treeitem"
        }
        return (tag_name in interactive_tags or
                element.get_attribute("role") in interactive_roles)

    def is_visible(self, element):
        return (
            element.is_displayed() and
            element.size['width'] > 0 and
            element.size['height'] > 0
        )

    def is_top_element(self, element) -> bool:
        """
        Checks if an element is the top-most element at its position.
        """
        try:
            # Get the element's bounding rectangle
            rect = element.rect
            center_x = rect['x'] + rect['width'] / 2
            center_y = rect['y'] + rect['height'] / 2

            # Scroll to the element to ensure it's within the viewport
            self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)

            # Use JavaScript to find the element at the center position
            script = "return document.elementFromPoint(arguments[0], arguments[1]);"
            top_element = self.driver.execute_script(script, center_x, center_y)

            # Compare the top-most element with the target element
            return top_element == element

        except Exception as e:
            print(f"Error while checking top element: {e}")
            return False


    def highlight_element(self, element, index):
        self.driver.execute_script(f"""
            var element = arguments[0];
            var overlay = document.createElement('div');
            overlay.style.position = 'absolute';
            overlay.style.border = '2px solid red';  // Use a fixed visible color for testing
            overlay.style.zIndex = '2147483647';
            var rect = element.getBoundingClientRect();
            overlay.style.top = rect.top + window.scrollY + 'px';
            overlay.style.left = rect.left + window.scrollX + 'px';
            overlay.style.width = rect.width + 'px';
            overlay.style.height = rect.height + 'px';
            // Create a label for the index
            var label = document.createElement('div');
            label.innerText = '{index}';
            label.style.position = 'absolute';
            label.style.top = '50%';
            label.style.left = '50%';
            label.style.transform = 'translate(-50%, -50%)';
            label.style.color = 'white';
            label.style.backgroundColor = 'black';
            label.style.padding = '2px 5px';
            label.style.borderRadius = '3px';
            // Append the label to the overlay
            overlay.appendChild(label);
            document.body.appendChild(overlay);
        """, element)

    def get_xpath(self, element):
        return self.driver.execute_script("""
        function getXPath(element) {
            if (element.id !== '') return 'id("' + element.id + '")';
            if (element === document.body) return element.tagName;
            var ix = 0;
            var siblings = element.parentNode.childNodes;
            for (var i = 0; i < siblings.length; i++) {
                var sibling = siblings[i];
                if (sibling === element) return getXPath(element.parentNode) + '/' + element.tagName + '[' + (ix + 1) + ']';
                if (sibling.nodeType === 1 && sibling.tagName === element.tagName) ix++;
            }
        }
        return getXPath(arguments[0]);
        """, element)

    def scroll_to_element(self, element):
        self.driver.execute_script("arguments[0].scrollIntoView();", element)

    def click_element(self, element):
        self.driver.execute_script("arguments[0].click();", element)

    def get_clickable_elements(self):
        interactable_elements = []
        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        all_elements = self.driver.find_elements(By.XPATH, "//*")
        for i, element in enumerate(all_elements):
            try:
                if self.is_interactable(element) and self.is_visible(element) and self.is_top_element(element):
                    self.highlight_element(element, i)
                    element_xpath = self.get_xpath(element)
                    element_id = element.get_attribute("id") or None
                    human_readable_element = {"index": i, "element_attributes": f"Tag: {element.tag_name}, Type: {element.get_attribute('type') or None}, Text: {element.text if element.text else None}, XPATH: {element_xpath if element_xpath else None}, Unique Element ID: {element_id}"}
                    interactable_elements.append(human_readable_element)
            except StaleElementReferenceException:
                continue
        return interactable_elements