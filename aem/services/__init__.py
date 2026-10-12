"""Business services.

Each module owns one area of the application, validates its inputs, writes
inside a single transaction, and records an audit entry for every change.
Services never contain financial arithmetic of their own: they delegate to
``aem.engine``.
"""
