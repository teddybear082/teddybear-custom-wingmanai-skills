import subprocess
import io
import sys
import traceback
import webbrowser
import os
import tempfile
from services.file import get_writable_dir
from typing import TYPE_CHECKING
from api.interface import (
    SettingsConfig, SkillConfig,
)
from api.enums import LogType
from skills.skill_base import Skill

if TYPE_CHECKING:
    from wingmen.open_ai_wingman import OpenAiWingman


class CodeExecutor(Skill):

    def __init__(
        self,
        config: SkillConfig,
        settings: SettingsConfig,
        wingman: "OpenAiWingman",
    ) -> None:
        super().__init__(config=config, settings=settings, wingman=wingman)
        self.skill_env_python = os.path.join(get_writable_dir("skills"), "code_executor", "python-embedded", "python.exe")
        self.installed_packages_file = os.path.join(get_writable_dir("skills"), "code_executor", f"installed_packages_{self.wingman.name}.txt")

    def get_tools(self) -> list[tuple[str, dict]]:
        tools = [
            (
                "write_and_execute_python_code",
                {
                    "type": "function",
                    "function": {
                        "name": "write_and_execute_python_code",
                        "description": "Writes Python code based on user request and executes it after user consent.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "code_request": {
                                    "type": "string",
                                    "description": "Description of the task the user wants the Python code to accomplish.",
                                },
                                "execute_code": {
                                    "type": "boolean",
                                    "description": "Flag indicating whether the user consents to execute the generated code.",
                                },
                                "python_code": {
                                    "type": "string",
                                    "description": "The actual python code necessary to accomplish the user's requested task.",
                                },
                            },
                            "required": ["code_request", "execute_code", "python_code"],
                        },
                    },
                },
            ),
            (
                "execute_shell_command",
                {
                    "type": "function",
                    "function": {
                        "name": "execute_shell_command",
                        "description": "Executes shell commands in the terminal or PowerShell.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": {
                                    "type": "string",
                                    "description": "The shell command to execute.",
                                },
                            },
                            "required": ["command"],
                        },
                    },
                },
            ),
            (
                "execute_external_python_script",
                {
                    "type": "function",
                    "function": {
                        "name": "execute_external_python_script",
                        "description": "Executes an external Python script using the virtual environment's Python.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "script_path": {
                                    "type": "string",
                                    "description": "The path to the external Python script.",
                                },
                                "requires_user_input": {
                                    "type": "boolean",
                                    "description": "Boolean indicating if the script requires user input. Defaults to False.",
                                },
                            },
                            "required": ["script_path"],
                        },
                    },
                },
            ),
            (
                "execute_pip_command",
                {
                    "type": "function",
                    "function": {
                        "name": "execute_pip_command",
                        "description": "Executes a pip command using the virtual environment's pip.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "pip_command": {
                                    "type": "string",
                                    "description": "The pip command to execute (e.g., install <package>).",
                                },
                            },
                            "required": ["pip_command"],
                        },
                    },
                },
            ),
            (
                "execute_web_code",
                {
                    "type": "function",
                    "function": {
                        "name": "execute_web_code",
                        "description": "Executes web code like HTML, saved in a file.",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "web_code_path": {
                                    "type": "string",
                                    "description": "The path to the web code file, like an index.html file, to run.",
                                },
                            },
                            "required": ["web_code_path"],
                        },
                    },
                 },
             ),
        ]
        return tools

    async def execute_tool(
        self, tool_name: str, parameters: dict[str, any]
    ) -> tuple[str, str]:
        function_response = "Error in processing the request. Please try again."
        instant_response = ""

        if tool_name == "write_and_execute_python_code":
            if self.settings.debug_mode:
                self.start_execution_benchmark()
                await self.printr.print_async(
                    f"Executing write_and_execute_python_code with parameters: {parameters}",
                    color=LogType.INFO,
                )

            code_request = parameters.get("code_request")
            execute_code = parameters.get("execute_code", False)
            code = parameters.get("python_code")

            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Generated code: {code}",
                    color=LogType.INFO,
                )

            function_response = f"Generated Python script:\n\n{code}"

            if execute_code:
                try:
                    # Redirect stdout and stderr to capture the output
                    old_stdout = sys.stdout
                    old_stderr = sys.stderr
                    new_stdout, new_stderr = io.StringIO(), io.StringIO()
                    sys.stdout, sys.stderr = new_stdout, new_stderr

                    exec(code)

                    # Reset stdout and stderr
                    sys.stdout = old_stdout
                    sys.stderr = old_stderr

                    stdout_output = new_stdout.getvalue()
                    stderr_output = new_stderr.getvalue() if new_stderr.getvalue() else "There were no errors and the code or command was executed successfully."

                    function_response += f"\n\nExecution Result:\nStdout:{stdout_output}\nStderr:{stderr_output}."
                except Exception as e:
                    error_trace = traceback.format_exc()
                    function_response += f"\n\nError executing the script: {str(e)}\n{error_trace}."

            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"{function_response}",
                    color=LogType.INFO,
                )

            if self.settings.debug_mode:
                await self.print_execution_time()

        elif tool_name == "execute_shell_command":
            command = parameters.get("command")
            try:
                result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
                if result.stderr:
                    error_output = result.stderr
                else:
                    error_output = "There were no errors and the code or command was executed successfully."
                function_response = f"Command Output:\n{result.stdout}\nError Output:\n{error_output}"
            except Exception as e:
                function_response = f"Error executing the command: {str(e)}"

            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Executed command: {command}\n{function_response}",
                    color=LogType.INFO,
                )

        elif tool_name == "execute_external_python_script":
            script_path = parameters.get("script_path")
            requires_input = parameters.get("requires_user_input")
            # If script requires input we need a command prompt window open otherwise we want to just run the script and return the result
            try:
                if requires_input:
                    command = f'start cmd /k "{self.skill_env_python} \"{script_path}\""'
                    subprocess.run(command, shell=True)
                    function_response = "Opened command prompt and started the Python script using the virtual environment's Python interpreter."
                else:
                    result = subprocess.run([self.skill_env_python, script_path], capture_output=True, text=True, timeout=60)
                    if result.stderr:
                        error_output = result.stderr
                    else:
                        error_output = "There were no errors and the code or command was executed successfully."
                    function_response = f"Script Output:\n{result.stdout}\nError Output:\n{error_output}"
            except Exception as e:
                function_response = f"Error executing the script: {str(e)}"
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Executed script: {script_path}\n{function_response}",
                    color=LogType.INFO,
                )

        elif tool_name == "execute_pip_command":
            pip_command = parameters.get('pip_command')
            # Catch if the LLM accidentally starts the command with pip
            if pip_command.startswith("pip"):
                pip_command = pip_command[3:].lstrip()
            try:
                result = subprocess.run([self.skill_env_python, "-m", "pip"] + pip_command.split(), capture_output=True, text=True)
                if result.stderr:
                    error_output = result.stderr
                else:
                    error_output = "There were no errors and the code or command was executed successfully."
                function_response = f"pip Command Output:\n{result.stdout}\nError Output:\n{error_output}"
            except Exception as e:
                function_response = f"Error executing the pip command: {str(e)}"
            if self.settings.debug_mode:
                await self.printr.print_async(
                    f"Executed pip command: {[self.skill_env_python, '-m', 'pip'] + pip_command.split()}\n{function_response}",
                    color=LogType.INFO,
                )
                
        elif tool_name == "execute_web_code":
            if self.settings.debug_mode:
                self.start_execution_benchmark()
                await self.printr.print_async(
                    f"Executing execute_web_code with parameters: {parameters}",
                    color=LogType.INFO,
                )

            code_file = parameters.get("web_code_path")

            try:
                if code_file and code_file != " ":
                    webbrowser.open('file://' + os.path.realpath(code_file), new=1)


                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"Running web code: {code_file}",
                        color=LogType.INFO,
                    )

                function_response = f"Ran web code:\n\n{code_file} on default browser"

            except Exception as e:

                if self.settings.debug_mode:
                    await self.printr.print_async(
                        f"Error running web code: {code_file}, \n\nError: {e}",
                        color=LogType.INFO,
                    )

                function_response = f"Error trying to run web code, {code_file}, error was: {e}."

        return function_response, instant_response

    async def is_waiting_response_needed(self, tool_name: str) -> bool:
        return True

    # Restore virtual environment back to original if any packages were installed
    def restore_environment(self):
        # save list of installed packages
        with open(self.installed_packages_file, "w") as f:
            result = subprocess.run([self.skill_env_python, "-m", "pip", "freeze"], capture_output=True, text=True)
            f.write(result.stdout)
        # uninstall all of them
        subprocess.run([self.skill_env_python, "-m", "pip", "uninstall", "-y", "-r", self.installed_packages_file], capture_output=True, text=True)
        # delete the file holding the installed packages
        if os.path.exists(self.installed_packages_file):
            os.remove(self.installed_packages_file)

    # At both start and end of use of skill, clean environment (helps catch if skill did not clean environment on unload because prematurely closed)
    async def prepare(self) -> None:
        self.restore_environment()

    async def unload(self) -> None:
        self.restore_environment()
        await super().unload()
