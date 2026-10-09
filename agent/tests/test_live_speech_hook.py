"""A's deletion runs in the API process, which imports routers only: the hook must ride on the router."""

from __future__ import annotations

import subprocess
import sys


def test_importing_the_router_registers_the_live_speech_hook() -> None:
    probe = (
        "import autune_agent.router\n"
        "from autune_core.deletion import registered_speech_modules\n"
        "assert 'agent_live' in registered_speech_modules(), registered_speech_modules()\n"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
