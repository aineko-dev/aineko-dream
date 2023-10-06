"""Example file showing how to create nodes."""

import os
import time
from typing import Optional, Union

import openai
import uvicorn
from aineko.core.node import AbstractNode
from dotenv import load_dotenv
from github import Auth, Github

from aineko_dream.config import API


class APIServer(AbstractNode):
    """Node that runs the API server.

    Uvicorn is the HTTP server that runs the FastAPI app.
    The endpoints and logic for the app is contained in coframe/api.
    """

    def _pre_loop_hook(self, params: Optional[dict] = None):
        """Initialize node state."""
        pass

    def _execute(self, params: dict):
        """Start the API server."""
        # pylint: disable=too-many-function-args
        self.log("Starting API server...")
        config = uvicorn.Config(
            params.get("app"),
            port=API.get("PORT"),
            log_level="info",
            host="0.0.0.0",
        )
        server = uvicorn.Server(config)
        server.run()


class CodeFetcher(AbstractNode):
    """Example node."""

    def _pre_loop_hook(self, params: Optional[dict] = None) -> None:
        """Optional; used to initialize node state."""
        load_dotenv()
        self.last_send = 0
        self.organization = params.get("organization")
        self.repo = params.get("repo")
        self.branch = params.get("branch")
        self.access_token = os.environ.get("GITHUB_ACCESS_TOKEN")
        self.file_contains = params.get("file_contains")
        self.retries = 0
        self.max_retries = 5
        self.retry_sleep = 5

        # Initialize github client
        auth = Auth.Token(token=self.access_token)
        self.github_client = Github(auth=auth)

        # Initialize code repo
        self.refresh_code()

    def _execute(self, params: Optional[dict] = None) -> Optional[bool]:
        """Required; function repeatedly executes.

        Accesses inputs via `self.consumer`, and outputs via
        `self.producer`.
        Logs can be sent via the `self.log` method.
        """
        # Check for new events from GitHub
        message = self.consumers["github_events"].consume()
        if message is None:
            return

        # Skip if event is not from the repo or branch we are tracking
        if (
            message["message"]["repository"]["organization"] != self.organization
            or message["message"]["repository"]["name"] != self.repo
            or message["message"]["ref"] != f"refs/heads/{self.branch}"
        ):
            return

        # Refresh code
        self.log("Received event from GitHub, fetching code from github")
        self.refresh_code()

    def refresh_code(self):
        """Refresh code from github."""
        repo_dict = self.download_github_code()
        cur_msg = {
            "organization": self.organization,
            "repo": self.repo,
            "branch": self.branch,
            "contents": repo_dict,
        }
        self.producers["repo_contents"].produce(cur_msg)
        self.log(
            f"Fetched code for {self.organization}/{self.repo} " f"branch {self.branch}"
        )

    def download_github_code(self) -> Union[dict, None]:
        """Download code from a github repo.

        Args:
            organization (str): github organization name
            repo (str): github repo name
            branch (str, optional): branch to download
            access_token (str): github access token

        Returns:
            dict: nested dictionary of files in repo
        """
        try:
            repo = self.github_client.get_repo(f"{self.organization}/{self.repo}")
            contents = repo.get_contents(self.file_contains, ref=self.branch)
            if isinstance(contents, list):
                return {f.path: f.decoded_content.decode("utf-8") for f in contents}
            return {contents.path: contents.decoded_content.decode("utf-8")}
        except Exception as err:  # pylint: disable=broad-except
            self.log(
                err,
                level="critical",
            )

        self.log(
            f"Unable to download {self.organization}/{repo} " f"branch {self.branch}",
            level="error",
        )
        if self.retries + 1 < self.max_retries:
            self.log("Retrying to get latest code...")
            self.retries += 1
            time.sleep(self.retry_sleep)
            self.download_github_code()
        else:
            self.log(
                "Exceeded maximum retries. Returning None.",
                level="critical",
            )
            return None


class Prompt(AbstractNode):
    """Example node."""

    def _pre_loop_hook(self, params: Optional[dict] = None) -> None:
        """Optional; used to initialize node state."""
        load_dotenv()
        self.documentation = None
        self.template = ""
        for prompt in ["guidelines", "documentation", "instructions"]:
            with open(f"aineko_dream/prompts/{prompt}", "r", encoding="utf-8") as f:
                self.template += f.read()
                self.template += "\n\n"
        self.model = params.get("model")
        self.max_tokens = params.get("max_tokens")
        self.temperature = params.get("temperature")
        openai.api_key = os.getenv("OPENAI_API_KEY")

    def _execute(self, params: Optional[dict] = None) -> Optional[bool]:
        """Required; function repeatedly executes.

        Accesses inputs via `self.consumer`, and outputs via
        `self.producer`.
        Logs can be sent via the `self.log` method.
        """
        # Update documentation with latest code
        repo_contents = self.consumers["repo_contents"].consume()
        if repo_contents is not None:
            self.log("Updating documentation...")
            self.documentation = repo_contents["message"]["contents"]

        user_prompt = self.consumers["user_prompt"].consume()
        if user_prompt is not None:
            # Skip if documentation is not available
            if self.documentation is None:
                self.log(
                    "No documentation available, "
                    "please wait for documentation to be built",
                    level="error",
                )
                return

            # Create prompt
            self.log("Creating prompt from user input...")
            prompt = self.template.format(
                instructions=user_prompt["message"]["prompt"],
                documentation=self.documentation,
            )
            self.producers["llm_prompt"].produce(prompt)
            messages = [{"role": "user", "content": str(prompt)}]

            # Query OpenAI LLM
            self.log("Querying OpenAI LLM...")
            try:
                response = openai.ChatCompletion.create(
                    messages=messages,
                    stream=False,
                    model=self.model,
                    max_tokens=self.max_tokens,
                    temperature=self.temperature,
                )
                self.log("OpenAI LLM response received")
                response_content = response.choices[0].message.content
                response = {
                    "dream": response_content,
                    "request_id": user_prompt["message"]["request_id"],
                }
                self.producers["llm_response"].produce(response)
            except openai.error.APIConnectionError:
                self.log(
                    "Error: Unable to connect to OpenAI LLM",
                    level="error",
                )
                self.producers["llm_response"].produce("ERROR")
                return


class LLMResponseFormatter(AbstractNode):
    """Example node."""

    def _pre_loop_hook(self, params: Optional[dict] = None) -> None:
        """Optional; used to initialize node state."""
        pass

    def _execute(self, params: Optional[dict] = None) -> Optional[bool]:
        """Required; function repeatedly executes.

        Accesses inputs via `self.consumer`, and outputs via
        `self.producer`.
        Logs can be sent via the `self.log` method.
        """
        llm_response = self.consumers["llm_response"].consume(how="next")
        if llm_response is None:
            return

        self.producers["formatted_response"].produce(llm_response["message"])


class PromptResponder(AbstractNode):
    """Node that emits the SMA of the yield data."""

    def _pre_loop_hook(self, params: Optional[dict] = None) -> None:
        """Optional; used to initialize node state."""
        self.state = {}
        self.cleanup_interval = params.get("cleanup_interval", 100)
        self.last_cleanup_time = time.time()

    def _execute(self, params: Optional[dict] = None) -> Optional[bool]:
        """Polls consumers, updates the state, and calculates the SMA."""
        # Update state df with new messages
        message = self.consumers["formatted_response"].consume()
        if message is None:
            return
        self.update_state(message["message"])
        self.producers["dream_responses"].produce(self.state)

    def update_state(self, message):
        """Updates the state with the latest APY values."""
        # Append to state dataframe
        self.state[message["request_id"]] = message
        self.state[message["request_id"]]["timestamp"] = time.time()

        # Cleanup state dataframe
        if time.time() - self.last_cleanup_time >= self.cleanup_interval:
            # Cleanup state dataframe
            self.state = {
                k: v
                for k, v in self.state.items()
                if time.time() - v["timestamp"] <= self.cleanup_interval
            }
            self.last_cleanup_time = time.time()
