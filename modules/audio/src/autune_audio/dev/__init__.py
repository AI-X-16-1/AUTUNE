"""A local-only page for putting a recording through the pipeline by hand.

Mounted by ``router`` only when ``AUTUNE_ENV=local``. It exists so the pipeline
can be judged on real audio before there is a frontend, and it is the one place
in module A that accepts an upload from a browser.

Nothing here is part of the product surface. It has no auth, so it must never be
reachable off a developer's machine.
"""

from .routes import router

__all__ = ["router"]
