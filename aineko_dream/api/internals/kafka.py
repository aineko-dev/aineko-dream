"""Kafka objects that are used across multiple endpoints.

Creates a kafka consumer and producer that are used for interacting
with the rest of the aineko pipeline.

To use, import the CONSUMERS and PRODUCER object from this file in the other
API files. Starting and ending is handled in main.py.
"""

import datetime
import json
from contextlib import asynccontextmanager

from aineko.config import AINEKO_CONFIG, DEFAULT_KAFKA_CONFIG
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI

from aineko_dream.config import API


@asynccontextmanager
async def start_kafka(
    app: FastAPI,
):  # pylint: disable=redefined-outer-name,unused-argument
    """Lifespan function to manage lifecycles of consumer and producer."""
    await CONSUMERS.start_consumers(API.get("CONSUMER_TOPICS"))
    await PRODUCER.start_producer()
    try:
        yield
    finally:
        await CONSUMERS.stop()
        await PRODUCER.stop()


class Consumers:
    """Class to hold multiple consumers."""

    def __init__(self):
        """Initialize consumers dict."""
        self.consumers = {}

    async def start_consumers(self, datasets: list):
        """Creates consumer objects and starts them."""
        for dataset in datasets:
            self.consumers[dataset] = AIOKafkaConsumer(
                dataset,
                bootstrap_servers=DEFAULT_KAFKA_CONFIG.get("BROKER_SERVER"),
                group_id=API.get("GROUP_ID"),
            )

            await self.consumers[dataset].start()

    async def stop(self):
        """Stop all consumers."""
        for consumer in self.consumers.values():
            await consumer.stop()

    async def update_offset(self, dataset: str):
        """Update the offset for a specific consumer."""
        offsets = await self.consumers[dataset].end_offsets(
            self.consumers[dataset].assignment()
        )
        for partition, offset in offsets.items():
            if offset == 0:
                continue
            self.consumers[dataset].seek(partition, offset)

    async def consume_latest_message(self, dataset: str):
        """Get latest message from consumer by updating the offsets."""
        await self.update_offset(dataset)
        message = await self.consumers[dataset].getone()
        return message


class Producer:
    """Class to hold producer."""

    def __init__(self):
        """Initialize producer."""
        self.producer = None

    async def start_producer(self):
        """Create producer and starts it."""
        self.producer = AIOKafkaProducer(
            bootstrap_servers=DEFAULT_KAFKA_CONFIG.get("BROKER_SERVER")
        )
        await self.producer.start()

    async def stop(self):
        """Stop producer."""
        await self.producer.stop()

    async def produce_message(self, dataset: str, message: dict, key: str = None):
        """Produce message to dataset.

        The "key" helps determine the partition to which the message will be
        written in Kafka. This allows filtering of messages by key.

        Args:
            dataset: Dataset to produce message to.
            message: Message to produce.
            key: Key to produce message with. Defaults to None.

        Returns:
            bool: True if message was successfully produced.
        """
        out_msg = {
            "timestamp": datetime.datetime.now().strftime(
                AINEKO_CONFIG.get("MSG_TIMESTAMP_FORMAT")
            ),
            "topic": dataset,
            "message": message,
        }
        out_msg = json.dumps(out_msg).encode("utf-8")

        await self.producer.send_and_wait(
            dataset, out_msg, encode_message(key)
        )

        return True


def encode_message(message: dict):
    """Encode message to bytes."""
    return bytes(str(message), encoding="utf-8")


CONSUMERS = Consumers()
PRODUCER = Producer()
