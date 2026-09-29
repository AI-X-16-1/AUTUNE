"""Repository-wide pytest setup.

A test run is never a deployment. Since #408 an unset ``AUTUNE_ENV`` means
production, which refuses the development secret key the moment anything
imports ``get_settings()`` -- so a checkout without a ``.env`` would fail at
collection. Default it to local for tests only; an explicit value still wins.
"""

import os

os.environ.setdefault("AUTUNE_ENV", "local")
