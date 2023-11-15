"""Example file setting default configs for project."""

from aineko.config import BaseConfig


# pylint: disable=invalid-name
class DEFAULT_CONFIG(BaseConfig):
    """Default configs for project."""

    NUM_CPUS = 0.5


class API(BaseConfig):
    """Config for API."""

    PORT = 8000
    BOOTSTRAP_SERVERS = "localhost:9092"
    GROUP_ID = "fastapi"
    CONSUMER_TOPICS = [
        "template-generator.response_cache",
    ]
