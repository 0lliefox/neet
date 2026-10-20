"""Keep credentials out of everything the capture writes to disk (health records, log lines).

requests puts the full URL, query string included, into connection errors, so a key sent as ``?api_key=`` (BODS)
ends up in the exception text. Redact both secret-looking query parameters and the literal values of secret
environment variables (names containing KEY, TOKEN, SECRET or PASS).
"""
from __future__ import annotations

import logging
import os
import re

MASK = "REDACTED"
_QUERY_SECRET = re.compile(r"\b((?:api_?key|key|token|access_token|client_secret|secret|password)=)[^&\s'\"]+", re.I)
_SECRET_ENV = re.compile(r"KEY|TOKEN|SECRET|PASS", re.I)


def _env_secrets() -> list[str]:
    vals = {v for k, v in os.environ.items() if _SECRET_ENV.search(k) and len(v) >= 8}
    return sorted(vals, key=len, reverse=True)


def redact(text: str) -> str:
    text = _QUERY_SECRET.sub(rf"\1{MASK}", text)
    for v in _env_secrets():
        text = text.replace(v, MASK)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))
