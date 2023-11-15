"""Fast API.

Endpoints are grouped in the api/routers directory.
A kafka consumer and producer are initialized in the start_kafka function.
These will be used by the api app to send and receive messages from the
aineko pipeline.
"""

import ast
import hashlib
import hmac
import os
import time
import uuid
from urllib.parse import parse_qs

import requests
from dotenv import load_dotenv
from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Request,
    Depends,
    Header,
    status,
)

from .internals.kafka import CONSUMERS, PRODUCER, start_kafka

load_dotenv()
TIMEOUT = 60 * 5  # seconds
app = FastAPI(lifespan=start_kafka)


"""
Shared functions
"""


async def fetch_latest_message(dataset: str):
    """Fetches latest message from the given dataset."""
    try:
        message = await CONSUMERS.consume_latest_message(dataset)
        # Decode and evaluate message
        message = ast.literal_eval(message.value.decode("utf-8"))
    except Exception as e:
        # Error handling if message cannot be fetched
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=500,
            detail=f"An error occurred while processing the request: {str(e)}",
        )
    return message["message"]


async def wait_and_post(request_id: str, response_url: str):
    """Wait for message from aineko pipeline and post result."""
    # Wait for correct response from aineko pipeline, look for request id
    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        response_cache = await fetch_latest_message("template-generator.response_cache")
        if request_id in response_cache:
            requests.post(
                response_url,
                json={"text": response_cache[request_id]["response"]},
                timeout=10,
            )
            return
        time.sleep(1)
    else:
        requests.post(
            response_url,
            json={"text": "Timeout while waiting for response."},
            timeout=10,
        )
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=408,
            detail="Timeout while waiting for response.",
        )


async def wait_response(request_id: str):
    """Wait for message from aineko pipeline and return."""
    # Wait for correct response from aineko pipeline, look for request id
    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        response_cache = await fetch_latest_message("template-generator.response_cache")
        if request_id in response_cache:
            return response_cache[request_id]["response"]
        time.sleep(1)
    else:
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=408,
            detail="Timeout while waiting for response.",
        )


"""
Slack
"""


def slack_request_validation(request):
    """Validate Salck request."""
    valid_company_request = (
        request["token"] == os.environ["SLACK_VERIFICATION_TOKEN_COMPANY"]
        and request["team_id"] == os.environ["SLACK_TEAM_ID_COMPANY"]
    )
    valid_os_request = (
        request["token"] == os.environ["SLACK_VERIFICATION_TOKEN_OS"]
        and request["team_id"] == os.environ["SLACK_TEAM_ID_OS"]
    )

    return valid_company_request or valid_os_request


@app.post("/slack/", status_code=200)
async def slack_pipeline_gen(request: Request, background_tasks: BackgroundTasks):
    """Create a new pipeline from a prompt."""
    request_body = await request.body()
    parsed_dict = parse_qs(request_body.decode("utf-8"))
    flattened_dict = {k: v[0] for k, v in parsed_dict.items()}
    if not slack_request_validation(flattened_dict):
        raise HTTPException(status_code=401, detail="Invalid request")
    # Send request to aineko pipeline using uuid as request id
    request_id = str(uuid.uuid4())
    request = {
        "request_id": request_id,
        "prompt": flattened_dict["text"],
    }
    await PRODUCER.produce_message("template-generator.user_prompt", request)

    # Wait for correct response from aineko pipeline, look for request id
    background_tasks.add_task(wait_and_post, request_id, flattened_dict["response_url"])
    return {"text": f"Aineko is dreaming of {flattened_dict['text']}..."}


"""
GitHub
"""


GITHUB_SECRET_TOKEN = os.environ[
    "GITHUB_WEBHOOK_SECRET"
]  # Set this to your GitHub webhook's secret token


def verify_github_signature(request: Request, body_data: bytes):
    """Verify GitHub signature."""
    x_hub_signature_sha1 = request.headers.get("X-Hub-Signature")
    x_hub_signature_sha256 = request.headers.get("X-Hub-Signature-256")

    if not x_hub_signature_sha1 and not x_hub_signature_sha256:
        raise HTTPException(status_code=400, detail="Missing X-Hub-Signature headers.")

    sha1_signature = hmac.new(
        bytes(GITHUB_SECRET_TOKEN, "utf-8"), msg=body_data, digestmod=hashlib.sha1
    ).hexdigest()
    sha256_signature = hmac.new(
        bytes(GITHUB_SECRET_TOKEN, "utf-8"), msg=body_data, digestmod=hashlib.sha256
    ).hexdigest()

    # Verification for SHA-1
    if x_hub_signature_sha1 and not hmac.compare_digest(
        sha1_signature, x_hub_signature_sha1.split("=")[1]
    ):
        raise HTTPException(
            status_code=400, detail="Invalid X-Hub-Signature (SHA1) signature."
        )

    # Verification for SHA-256
    if x_hub_signature_sha256 and not hmac.compare_digest(
        sha256_signature, x_hub_signature_sha256.split("=")[1]
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid X-Hub-Signature-256 (SHA256) signature.",
        )

    return True


@app.post("/github-webhook/")
async def handle_github_event(request: Request):
    """Handle github event."""
    body_data = await request.body()
    if verify_github_signature(request, body_data):
        payload = await request.json()
    print("Received GitHub webhook event...")
    await PRODUCER.produce_message("template-generator.github_events", payload)
    return {"status": "event processed"}


"""
Test
"""

TEST_API_KEY = os.environ[
    "TEST_API_KEY"
]  # Set this to your GitHub webhook's secret token


def validate_test_api_key(x_api_key: str = Header(...)):
    """Validates API key."""
    if not TEST_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="API key is not set in the environment.",
        )
    if x_api_key != TEST_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API Key",
        )
    return x_api_key


@app.get("/test/", status_code=200)
async def test_pipeline_gen(prompt: str, api_key: str = Depends(validate_test_api_key)):
    """Create a new pipeline from a prompt."""
    # Send request to aineko pipeline using uuid as request id
    request_id = str(uuid.uuid4())
    request = {
        "request_id": request_id,
        "prompt": prompt,
    }
    await PRODUCER.produce_message("template-generator.user_prompt", request)

    # Wait for correct response from aineko pipeline, look for request id
    response = await wait_response(request_id)
    # Save response to a file
    with open(f"LATEST_RESPONSE.md", "w") as f:
        f.write(response)
    return response
