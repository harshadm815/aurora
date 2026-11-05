"""
The log file must never contain what the user typed, what AURORA
remembered, or what the model replied. Only metadata (intents, tool
names, attribute names, ids, counts) may be logged.
"""

import logging

import httpx

from aurora.brain.brain import Brain
from aurora.kernel.engine import AuroraEngine
from aurora.tool_plugins.registry import default_tools

from conftest import FakeProvider
from test_weather import client_returning

# Distinctive words that appear only in user input or model output.
SECRETS = [
    "Zorblax",        # name
    "Quillhaven",     # place
    "hunter2",        # password
    "Fennimore",      # note content
    "Glimmerwick",    # model reply
    "7731",           # arithmetic
]

MESSAGES = [
    "my name is Zorblax and I live in Quillhaven",
    "my password is hunter2",
    "I like Fennimore pie",
    "remember that the Fennimore meeting is on Friday",
    "what do you know about Fennimore?",
    "who am I?",
    "forget the Fennimore meeting",
    "yes",
    "what is 7731 * 2?",
    "what's the weather in Quillhaven?",
    "route me somewhere",
    "",
]


def run_everything(memory, client):
    engine = AuroraEngine(
        Brain(FakeProvider(reply="Glimmerwick says hello")),
        memory=memory,
        tools=default_tools(memory, client),
    )
    engine.start()
    for message in MESSAGES:
        engine.process_message(message)
    engine.shutdown()


def assert_no_secrets(text):
    leaked = [secret for secret in SECRETS if secret.lower() in text.lower()]
    assert leaked == [], f"Log contains user content: {leaked}"


def test_no_user_content_in_logs(memory, caplog):
    caplog.set_level(logging.DEBUG, logger="aurora")

    run_everything(memory, client_returning())

    assert "Route:" in caplog.text            # Logging did happen…
    assert "Memory added: Name" in caplog.text
    assert_no_secrets(caplog.text)            # …without any user content.


def test_no_user_content_in_logs_when_tools_fail(memory, caplog):
    caplog.set_level(logging.DEBUG, logger="aurora")

    run_everything(memory, client_returning(error=httpx.ConnectError("offline")))
    run_everything(memory, client_returning(geocode={}))

    assert_no_secrets(caplog.text)
