from __future__ import annotations

from .application.query.receipt import *  # noqa: F401,F403

# Keep the deprecated compatibility namespace frozen. New canonical helpers
# remain available from fossil_core.application.query.receipt without leaking
# into the legacy star-import surface.
del build_failed_query_execution_receipt
