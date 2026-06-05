"""Built-in executors. Importing this package registers them."""

from __future__ import annotations

from orc.effects.builtin import (
    fs_write,  # noqa: F401  (import registers the executor)
    gmail,  # noqa: F401  (import registers the executor)
)
