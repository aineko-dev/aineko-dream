"""Fast API.

Endpoints are grouped in the api/routers directory.
A kafka consumer and producer are initialized in the start_kafka function.
These will be used by the api app to send and receive messages from the
aineko pipeline.
"""
import json
from typing import Dict, Annotated
import hashlib
import hmac
import os
import time
import uuid
import urllib

import requests
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

from .internals.kafka import CONSUMERS, PRODUCER, start_kafka

load_dotenv()
GITHUB_WEBHOOK_SECRET = os.environ[
    "GITHUB_WEBHOOK_SECRET"
]  # Set this to your GitHub webhook's secret token
TIMEOUT = 60 * 2

app = FastAPI(lifespan=start_kafka)


async def wait_for_message(request_id: str) -> Dict[str, str]:
    """Wait for message from aineko pipeline."""
    # Wait for correct response from aineko pipeline, look for request id
    start_time = time.time()
    while time.time() - start_time < TIMEOUT:
        # Fetches aineko dream responses
        try:
            message = await CONSUMERS.consume_latest_message("response_cache")
            message = json.loads(message.value.decode("utf-8"))
        except Exception as e:
            raise HTTPException(  # pylint: disable=raise-missing-from
                status_code=500,
                detail=f"An error occurred while processing the request: {str(e)}",
            )
        response_cache = message["message"]
        if request_id in response_cache:
            return response_cache[request_id]
        time.sleep(0.5)
    else:
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=408,
            detail="Timeout while waiting for response.",
        )


async def wait_and_send(request_id: str, response_url: str) -> None:
    """Wait for message from aineko pipeline and send it to slack."""
    # Wait for correct response from aineko pipeline, look for request id
    try:
        response = await wait_for_message(request_id)
        requests.post(
            response_url,
            json={"text": response["response"]},
            timeout=10,
        )
        return
    except Exception as err: # pylint: disable=broad-except
        requests.post(
            response_url,
            json={"text": str(err)},
            timeout=10,
        )
        raise err


@app.post("/aineko-dream-dev/", status_code=200)
async def code_gen(
    request: Request, background_tasks: BackgroundTasks
) -> Dict[str, str]:
    """Create a new project from a prompt."""
    # Parse request body
    try:
        request_body = await request.body()
        parsed_dict = urllib.parse.parse_qs(request_body.decode("utf-8"))
        flattened_dict = {k: v[0] for k, v in parsed_dict.items()}
    except Exception as e:
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=500,
            detail=f"Unable to parse request. Got the following error: {str(e)}",
        )

    # Verify request is coming from slack
    try:
        is_token_valid = request["token"] == os.environ["SLACK_VERIFICATION_TOKEN"]
        is_team_id_valid = request["team_id"] == os.environ["SLACK_TEAM_ID"]
    except ValueError as e:
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=500,
            detail=f"Unable to parse request. Got the following error: {str(e)}",
        )
    if not (is_token_valid and is_team_id_valid):
        raise HTTPException(status_code=401, detail="Invalid request")

    # Send request to aineko pipeline using uuid as request id
    request_id = str(uuid.uuid4())
    request = {
        "request_id": request_id,
        "prompt": flattened_dict["text"],
    }
    await PRODUCER.produce_message("user_prompt", request)

    # Wait for response from aineko pipeline and send to slack
    background_tasks.add_task(
        wait_and_send, request_id, flattened_dict["response_url"]
    )
    return {"text": f"Aineko is dreaming of {flattened_dict['text']}..."}


@app.post("/github-webhook/")
async def handle_github_event(request: Request) -> Dict[str, str]:
    """Handle github event."""
    body_data = await request.body()

    # Verification for SHA-256
    x_hub_signature_sha256 = request.headers.get("X-Hub-Signature-256")
    if x_hub_signature_sha256:
        raise HTTPException(status_code=400, detail="Missing X-Hub-Signature headers.")
    sha256_signature = hmac.new(
        bytes(GITHUB_WEBHOOK_SECRET, "utf-8"), msg=body_data, digestmod=hashlib.sha256
    ).hexdigest()
    if x_hub_signature_sha256 and not hmac.compare_digest(
        sha256_signature, x_hub_signature_sha256.split("=")[1]
    ):
        raise HTTPException(
            status_code=400,
            detail="Invalid X-Hub-Signature-256 (SHA256) signature.",
        )

    # Write payload to dataset
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(  # pylint: disable=raise-missing-from
            status_code=500,
            detail=f"Unable to parse request. Got the following error: {str(e)}",
        )
    await PRODUCER.produce_message("github_events", payload)
    return {"status": "event processed"}


@app.get("/aineko-dream-test/{prompt}")
async def code_gen_test(prompt: Annotated[str, "prompt to generate code"]) -> Dict[str, str]:
    """Create a new project from a prompt."""
    # Send request to aineko pipeline using uuid as request id
    request_id = str(uuid.uuid4())
    user_prompt = {
        "request_id": request_id,
        "prompt": prompt,
    }
    await PRODUCER.produce_message("user_prompt", user_prompt)

    # Wait for response from aineko pipeline and return
    response = await wait_for_message(request_id)
    return {"text": response["response"]}
