"""Run the bot in socket mode for local development.

uv run python -m autune_bot
"""

from __future__ import annotations

from slack_bolt.adapter.socket_mode import SocketModeHandler

from autune_core import get_settings

from .app import build_app

if __name__ == "__main__":
    settings = get_settings()
    SocketModeHandler(build_app(), settings.slack_app_token).start()
