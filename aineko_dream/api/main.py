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
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from .internals.kafka import CONSUMERS, PRODUCER, start_kafka

load_dotenv()
app = FastAPI(lifespan=start_kafka)


def is_request_valid(request):
    """Validate request."""
    is_token_valid = request["token"] == os.environ["SLACK_VERIFICATION_TOKEN"]
    is_team_id_valid = request["team_id"] == os.environ["SLACK_TEAM_ID"]

    return is_token_valid and is_team_id_valid


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


async def wait_for_message(request_id: str, response_url: str):
    """Wait for message from aineko pipeline and send it to slack."""
    # Wait for correct response from aineko pipeline, look for request id
    timeout = 60 * 2
    start_time = time.time()
    while time.time() - start_time < timeout:
        response_cache = await fetch_latest_message("dream_responses")
        if request_id in response_cache:
            requests.post(
                response_url,
                json={"text": response_cache[request_id]["dream"]},
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


@app.post("/aineko-dream-dev/", status_code=200)
async def code_gen(request: Request, background_tasks: BackgroundTasks):
    """Create a new project from a prompt."""
    request_body = await request.body()
    parsed_dict = parse_qs(request_body.decode("utf-8"))
    flattened_dict = {k: v[0] for k, v in parsed_dict.items()}
    if not is_request_valid(flattened_dict):
        raise HTTPException(status_code=401, detail="Invalid request")
    # Send request to aineko pipeline using uuid as request id
    request_id = str(uuid.uuid4())
    request = {
        "request_id": request_id,
        "prompt": flattened_dict["text"],
    }
    await PRODUCER.produce_message("user_prompt", request)

    # Wait for correct response from aineko pipeline, look for request id
    background_tasks.add_task(
        wait_for_message, request_id, flattened_dict["response_url"]
    )
    return {"text": f"Aineko is dreaming of {flattened_dict['text']}..."}


SECRET_TOKEN = os.environ[
    "GITHUB_WEBHOOK_SECRET"
]  # Set this to your GitHub webhook's secret token


def verify_signature(request: Request, body_data: bytes):
    """Verify signature."""
    x_hub_signature_sha1 = request.headers.get("X-Hub-Signature")
    x_hub_signature_sha256 = request.headers.get("X-Hub-Signature-256")

    if not x_hub_signature_sha1 and not x_hub_signature_sha256:
        raise HTTPException(
            status_code=400, detail="Missing X-Hub-Signature headers."
        )

    sha1_signature = hmac.new(
        bytes(SECRET_TOKEN, "utf-8"), msg=body_data, digestmod=hashlib.sha1
    ).hexdigest()
    sha256_signature = hmac.new(
        bytes(SECRET_TOKEN, "utf-8"), msg=body_data, digestmod=hashlib.sha256
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
    if verify_signature(request, body_data):
        payload = await request.json()
    print("Received GitHub webhook event...")
    await PRODUCER.produce_message("github_events", payload)
    return {"status": "event processed"}
