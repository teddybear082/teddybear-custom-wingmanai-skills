import time
import os
from datetime import datetime
from io import BytesIO
import numpy as np
import base64
import requests
import cv2
from PIL import Image, ImageGrab, ImageDraw, ImageFont
from typing import TYPE_CHECKING
from api.interface import SettingsConfig, SkillConfig
from api.enums import LogSource, LogType
from services.benchmark import Benchmark
from services.file import get_writable_dir
from skills.skill_base import Skill
import mouse.mouse as mouse
import keyboard.keyboard as keyboard
if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman

class ComputerControl(Skill):
    def __init__(self, config: SkillConfig, settings: SettingsConfig, wingman: "OpenAiWingman") -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.server_url = "http://localhost:8000/parse/"
        # Take an initial test screen shot to cache screen resolution for later use
        screenshot = ImageGrab.grab()
        width, height = screenshot.size
        self.screen_width = width
        self.screen_height = height
        self.prompt = ""
        self.gif_prompt = ""
        self.last_action = ""
        self.images_cache : list = []
        self.max_steps_for_one_prompt : int = 20
        self.current_step_for_prompt : int = 0

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            ("parse_screen", {
                "type": "function",
                "function": {
                    "name": "parse_screen",
                    "description": "Analyzes the user's screen to get the list of interactable elements on the screen.",
                    "parameters": {}
                }
            }),
            ("left_click_element", {
                "type": "function",
                "function": {
                    "name": "left_click_element",
                    "description": "Generates a left mouse click at the element's coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "The bounding box of the element."
                            }
                        },
                        "required": ["bbox"]
                    }
                }
            }),
            ("double_click_element", {
                "type": "function",
                "function": {
                    "name": "double_click_element",
                    "description": "Generates a double click at the element's coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "The bounding box of the element."
                            }
                        },
                        "required": ["bbox"]
                    }
                }
            }),
            ("right_click_element", {
                "type": "function",
                "function": {
                    "name": "right_click_element",
                    "description": "Generates a right mouse click at the element's coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "The bounding box of the element."
                            }
                        },
                        "required": ["bbox"]
                    }
                }
            }),
            ("scroll", {
                "type": "function",
                "function": {
                    "name": "scroll",
                    "description": "Scrolls up or down at the specified coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "The bounding box of the element."
                            },
                            "scroll_up": {
                                "type": "boolean",
                                "description": "True to scroll up, False to scroll down."
                            }
                        },
                        "required": ["bbox", "scroll_up"]
                    }
                }
            }),
            ("type_content", {
                "type": "function",
                "function": {
                    "name": "type_content",
                    "description": "Types content at the specified text box coordinates.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "bbox": {
                                "type": "array",
                                "items": {"type": "number"},
                                "description": "The bounding box of the text box."
                            },
                            "content_to_type": {
                                "type": "string",
                                "description": "The content to type."
                            }
                        },
                        "required": ["bbox", "content_to_type"]
                    }
                }
            }),
            ("press_enter", {
                "type": "function",
                "function": {
                    "name": "press_enter",
                    "description": "Presses the enter key.",
                    "parameters": {}
                }
            }),
            ("create_gif_of_task_completed", {
                "type": "function",
                "function": {
                    "name": "create_gif_of_task_completed",
                    "description": "Creates a .gif to commemorate the completion of the goal the user set with the ComputerControl skill.",
                    "parameters": {}
                }
            }),
        ]
        return tools

    async def execute_tool(self, tool_name: str, parameters: dict[str, any], benchmark: Benchmark) -> tuple[str, str]:
        function_response = "Error in execution. Can you please try your command again?"
        instant_response = ""
        benchmark.start_snapshot(f"ComputerControl: {tool_name}")
        if self.settings.debug_mode:
            message = f"ComputerControl: executing tool '{tool_name}'"
            if parameters:
                message += f" with params: {parameters}"
            await self.printr.print_async(text=message, color=LogType.INFO)
        if tool_name == "parse_screen":
            try:
                # If current prompt is the same one we're already using for this task, increase step count
                if self.prompt == self.gif_prompt:
                    self.current_step_for_prompt += 1
                else:
                    self.current_step_for_prompt = 1
                self.gif_prompt = self.prompt
                screenshot = ImageGrab.grab()
                frame = np.array(screenshot)
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                _, buffer = cv2.imencode('.png', frame)
                base64_image = base64.b64encode(buffer).decode('utf-8')
                data = {'base64_image': base64_image}
                response = requests.post(self.server_url, json=data)
                parsed_content = response.json().get("parsed_content_list")
                # Assign Box IDs to parsed content to make it easier for AI to match up annotated screenshot and parsed elements.
                count : int = 0
                for dictionary in parsed_content:
                    dictionary["Box ID"] = count
                    count += 1
                    # Albeit minimal, reduce token count by removing unnecessary dictionary entries describing source of information, e.g, 'source': 'box_yolo_content_yolo'
                    try:
                        del dictionary["source"]
                    except:
                        continue
                bounded_box_base64_image = response.json().get("som_image_base64")
                await self.printr.print_async(
                    "Analyzing this image",
                    color=LogType.INFO,
                    source=LogSource.WINGMAN,
                    source_name=self.wingman.name,
                    skill_name=self.name,
                    additional_data={"image_base64": bounded_box_base64_image},
                )
                boxes_screenshot = Image.open(BytesIO(base64.b64decode(bounded_box_base64_image)))
                self.images_cache.append(boxes_screenshot)
                system_prompt = await self.get_llm_system_message(self.prompt, self.last_action, parsed_content)
                if self.settings.debug_mode:
                    await self.printr.print_async(text=f"Prompting llm to analyze image with pending user request: {self.prompt}, last AI action: {self.last_action} and parsed_content: {parsed_content}", color=LogType.INFO)
                messages = [
                    {
                        "role": "system",
                        "content": system_prompt
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"Keeping the user's ultimate goal in mind, which the user said was '{self.prompt}', determine the next action to take on which screen element (by BoxID) in the attached picture."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{bounded_box_base64_image}",
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
                function_response = f"After analyzing the user's screen the next recommended course of action is as follows: {llm_response}."
                if self.current_step_for_prompt >= self.max_steps_for_one_prompt:
                    function_response = "ERROR: You must STOP completing this task.  Maximum steps reached.  Inform the user they have reached the maximum steps allowed."
                if self.settings.debug_mode:
                    await self.printr.print_async(text=function_response, color=LogType.INFO)
                
            except Exception as e:
                function_response = f"Error: {e}"
                if self.settings.debug_mode:
                    debug_message = f" Parse screen error '{e}'"
                    await self.printr.print_async(text=debug_message, color=LogType.INFO)
            
        elif tool_name in ["left_click_element", "double_click_element", "right_click_element", "scroll", "type_content"]:
            try:
                x_min, y_min, x_max, y_max = parameters.get("bbox")
                click_x = ((x_min + x_max) / 2) * self.screen_width
                click_y = ((y_min + y_max) / 2) * self.screen_height
                duration=1.0
                
                if tool_name == "left_click_element":
                    mouse.move(click_x, click_y, duration)
                    time.sleep(0.2)
                    mouse.press(button="left")
                    time.sleep(0.2)
                    mouse.release(button="left")
                    function_response = "Left clicked at the specified coordinates."

                if tool_name == "double_click_element":
                    mouse.move(click_x, click_y, duration)
                    time.sleep(0.2)
                    mouse.double_click(button="left")
                    time.sleep(0.2)
                    function_response = "Double clicked at the specified coordinates."
                    
                elif tool_name == "right_click_element":
                    mouse.move(click_x, click_y, duration)
                    time.sleep(0.2)
                    mouse.press(button="right")
                    time.sleep(0.2)
                    mouse.release(button="right")
                    function_response = "Right clicked at the specified coordinates."

                elif tool_name == "scroll":
                    scroll_up = parameters.get("scroll_up")
                    mouse.move(click_x, click_y, duration)
                    time.sleep(0.2)
                    mouse.wheel(100 if scroll_up else -100)
                    function_response = "Scrolled at the specified coordinates."

                elif tool_name == "type_content":
                    content_to_type = parameters.get("content_to_type")
                    mouse.move(click_x, click_y, duration)
                    time.sleep(0.2)
                    mouse.press(button="left")
                    time.sleep(0.2)
                    mouse.release(button="left")
                    time.sleep(0.2)
                    keyboard.write(content_to_type, delay=0.01, hold=0.01)
                    function_response = f"Typed content: {content_to_type}"

            except Exception as e:
                function_response = f"Error attempting to perform computer control action: {tool_name}, error was: {e}."

        elif tool_name == "press_enter":
            keyboard.press("enter")
            time.sleep(0.2)
            keyboard.release("enter")
            function_response = "Pressed Enter key."
            
        elif tool_name == "create_gif_of_task_completed":
            try:
                # take a final screenshot to ensure we capture task completion
                screenshot = ImageGrab.grab()
                self.images_cache.append(screenshot)
                created_gif_path = self.create_gif(self.gif_prompt, self.images_cache)
                function_response = f"Gif created of task completed with ComputerControl skill.  The file is at: {created_gif_path}."
            except Exception as e:
                function_response = f"There was an error creating the gif: {e}.  Just skip creating the gif for now."
            # reset prompt, last action, and images cache
            self.prompt = ""
            self.gif_prompt = ""
            self.last_action = ""
            self.images_cache = []

        benchmark.finish_snapshot()
        time.sleep(0.5)
        return function_response, instant_response

    async def get_llm_system_message(self, prompt: str, last_action: str, screen_info: list) -> str:
        system_message = f"""
        You are using a computer.
        You are able to use a mouse and keyboard to interact with the computer based on the given task and screenshot.
        You can only interact with the desktop GUI (no terminal or application menu access).

        You may be given some history plan and actions, this is the response from the previous loop.
        You should carefully consider your plan base on the task, screenshot, and history actions.

        Here is the list of all detected bounding boxes by IDs on the screen and their description:{screen_info}

        Your available "Next Action" only include:
        - type: types a string of text.
        - left_click: move mouse to box id and left clicks.
        - right_click: move mouse to box id and right clicks.
        - double_click: move mouse to box id and double clicks.
        - move: move mouse to box id.
        - scroll_up: scrolls the screen up to view previous content.
        - scroll_down: scrolls the screen down, when the desired button is not visible, or you need to see more content. 
        - USER INTERVENTION: call this when you reach a roadblock and need to explain to the user their help is required
        - NONE - CREATE COMPLETION GIF: call this when you have completed the user's goal.

        Based on the visual information from the screenshot image and the detected bounding boxes, please determine the next action, and the Box ID you should operate on in order to contimue toward completimg the task.

        Output format:
        ```json
        {{
            "Reasoning": str, # describe what is in the current screen, taking into account the history, then describe your step-by-step thoughts on how to achieve the task, choose one action from available actions at a time.
            "Next Action": "action_type, action description" | "None" # one action at a time, describe it in short and precisely. 
            "Box ID": n,
            "bbox": [x, y, x, y],
        }}
        ```

        One Example:
        ```json
        {{  
            "Reasoning": "The current screen shows google result of amazon, in previous action I have searched amazon on google. Then I need to click on the first search results to go to amazon.com.",
            "Next Action": "left_click",
            "Box ID": m
            "bbox": [x, y, x, y]
        }}
        ```

        Another Example:
        ```json
        {{
            "Reasoning": "The current screen shows the front page of amazon. There is no previous action. Therefore I need to type "Apple watch" in the search bar.",
            "Next Action": "type",
            "Box ID": n,
            "bbox": [x, y, x, y],
            "content_to_type": "Apple watch"
        }}
        ```

        Another Example:
        ```json
        {{
            "Reasoning": "The current screen does not show 'submit' button, I need to scroll down to see if the button is available.",
            "Next Action": "scroll_down",
            "Box ID": n,
            "bbox": [x, y, x, y]
        }}
        ```

        IMPORTANT NOTES:
        1. You should only give a single action at a time.
        2. The user's current overrarching objective is: {prompt}.
        3. The last action performed if any was: {last_action}.
        4. Attach the next action prediction in the "Next Action".
        5. You should not include other actions, such as keyboard shortcuts.
        6. When the task is completed, don't complete additional actions. You should say "Next Action": "NONE - CREATE COMPLETION GIF" in the json field.
        7. The objective may involve buying multiple products or navigating through multiple pages. You should break it into subgoals and complete each subgoal one by one in the order of the instructions.
        8. Avoid choosing the same action/elements multiple times in a row, if it happens, reflect to yourself, what may have gone wrong, and predict a different action.
        9. If you are prompted with login information page or captcha page that you cannot complete, you should say "Next Action": "USER INTERVENTION" in the json field and in your reasoning explain what the user should be asked to do to intervene.
        """
        return system_message


    def create_gif(self, prompt: str, images: list) -> str:
        # Create the name for the GIF file
        date_str = datetime.now().strftime("%m%d%y")
        gif_name = f"ComputerControl_{date_str}_{'_'.join(prompt.split()[:3])}.gif"
        
        # Get the writable directory and prepare the full path
        directory_path = get_writable_dir("files")
        gif_path = os.path.join(directory_path, gif_name)
        # Create the first image based on the prompt
        prompt_image = Image.new("RGB", (640, 480), "black")
        draw = ImageDraw.Draw(prompt_image)
        
        # Load a font
        font_size = 32
        font = ImageFont.truetype("arial.ttf", font_size)
        
        # Split prompt for frame
        multiline_prompt = self.split_prompt_for_gif("User Request: " + prompt)

        # Check text size and resize font if necessary
        while True:
            bbox = draw.textbbox((0, 0), multiline_prompt, font=font)
            text_size = (bbox[2] - bbox[0], bbox[3] - bbox[1])
            if text_size[0] <= 620 and text_size[1] <= 460:  # Adjust these values if needed
                break
            font_size -= 1
            font = ImageFont.truetype("arial.ttf", font_size)

        draw.text((10, 10), multiline_prompt, fill=(255, 255, 255), font=font)

        image_list = [prompt_image]
        # Append already existing PIL Image objects
        for img in images:
            img.thumbnail((640, 480))
            image_list.append(img)
        # Save as GIF
        image_list[0].save(gif_path, save_all=True, append_images=image_list[1:], loop=0, duration=3000)
        return gif_path

    def split_prompt_for_gif(self, prompt, max_words=5):
        words = prompt.split()
        lines = []

        for i in range(0, len(words), max_words):
            line = ' '.join(words[i:i + max_words])
            lines.append(line)

        multiline_string = '\n'.join(lines)
        return multiline_string

    async def on_add_user_message(self, message: str) -> None:
        self.prompt = message

    async def on_add_assistant_message(self, message: str, tool_calls: list) -> None:
        self.last_action = str(tool_calls)
    