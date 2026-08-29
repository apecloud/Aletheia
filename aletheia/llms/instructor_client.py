"""Shared instructor-over-litellm client factory.

Extracted from eight agent files that each wrote the identical
``instructor.from_litellm(completion)`` line to build their structured-output
client. No configuration varies at construction time across those call
sites -- per-call variance (model, response_model, messages) all happens
later at ``.chat.completions.create(...)`` time -- so one parameterless
factory is a safe drop-in replacement for all of them.
"""

from __future__ import annotations

import instructor
from litellm import completion


def get_instructor_client():
    """Return a fresh instructor client wrapping litellm's completion()."""
    return instructor.from_litellm(completion)
