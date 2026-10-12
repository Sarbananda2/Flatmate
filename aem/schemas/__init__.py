"""Typed request and response models.

Requests are validated here before any service is called, so the API rejects
malformed input with a consistent, machine-readable error instead of letting it
reach the database. Money fields are named ``*_paise`` and typed ``int``
throughout -- there is no float anywhere in a request or response model.
"""
from .models import *  # noqa: F401,F403
from .models import __all__  # noqa: F401
