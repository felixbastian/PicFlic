import json
import logging

from src.logging_config import ActionContextFilter, JsonFormatter
from src.logging_context import bind_log_context, clear_log_context, reset_log_context


def test_json_formatter_includes_trace_fields_and_extra_payload():
    clear_log_context()
    token = bind_log_context(
        process_id="telegram-abc123",
        user_id="user-123",
        telegram_user_id="42",
        update_id="9001",
        action="telegram_message",
        workflow="nutrition",
    )
    try:
        record = logging.LogRecord(
            name="src.bot",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="Structured log",
            args=(),
            exc_info=None,
        )
        record.event = "bot_event"
        record.analysis = {"category": "food"}
        ActionContextFilter().filter(record)

        payload = json.loads(JsonFormatter().format(record))

        assert payload["process_id"] == "telegram-abc123"
        assert payload["user_id"] == "user-123"
        assert payload["telegram_user_id"] == "42"
        assert payload["update_id"] == "9001"
        assert payload["action"] == "telegram_message"
        assert payload["workflow"] == "nutrition"
        assert payload["event"] == "bot_event"
        assert payload["analysis"] == {"category": "food"}
        assert payload["logger"] == "src.bot"
        assert payload["message"] == "Structured log"
    finally:
        reset_log_context(token)
        clear_log_context()
