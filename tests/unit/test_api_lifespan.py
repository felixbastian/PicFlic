import asyncio

import src.api as api


class _FakeTelegramApplication:
    def __init__(self, *, running: bool):
        self.running = running
        self.calls: list[str] = []

    async def stop(self) -> None:
        self.calls.append("stop")

    async def shutdown(self) -> None:
        self.calls.append("shutdown")


def test_shutdown_telegram_application_skips_stop_when_not_running():
    application = _FakeTelegramApplication(running=False)

    asyncio.run(api._shutdown_telegram_application(application, "Telegram application"))

    assert application.calls == ["shutdown"]


def test_shutdown_telegram_application_stops_running_application_first():
    application = _FakeTelegramApplication(running=True)

    asyncio.run(api._shutdown_telegram_application(application, "Telegram application"))

    assert application.calls == ["stop", "shutdown"]
