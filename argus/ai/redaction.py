"""Redaction: mask sensitive values before any text is sent to an external LLM.

Masks IPv4/IPv6 addresses (with prefix), device serial numbers and secret-looking key/value
pairs. Tokens are consistent within one request (same IP -> same token) so the model can still
reason about relationships, and :meth:`Redactor.restore` puts real values back in the answer.
"""

from __future__ import annotations

import re

IPV4 = re.compile(r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:/\d{1,2})?\b")
IPV6 = re.compile(r"\b(?:[0-9a-fA-F]{1,4}:){2,7}[0-9a-fA-F]{1,4}(?:/\d{1,3})?\b")
SERIAL = re.compile(r"\b(?:FDO|WZP|SAL|FOC|FGT|PA)[A-Z0-9]{6,12}\b")
SECRET = re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key|community|psk)(\s*[:=]\s*)(\S+)")


class Redactor:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._fwd: dict[str, str] = {}
        self._rev: dict[str, str] = {}
        self._counts: dict[str, int] = {}

    def _token(self, kind: str, value: str) -> str:
        if value in self._fwd:
            return self._fwd[value]
        self._counts[kind] = self._counts.get(kind, 0) + 1
        tok = f"<{kind}_{self._counts[kind]}>"
        self._fwd[value] = tok
        self._rev[tok] = value
        return tok

    def redact(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        text = SECRET.sub(lambda m: f"{m.group(1)}{m.group(2)}<SECRET>", text)
        text = IPV4.sub(lambda m: self._token("IP", m.group(0)), text)
        text = IPV6.sub(lambda m: self._token("IP6", m.group(0)), text)
        text = SERIAL.sub(lambda m: self._token("SERIAL", m.group(0)), text)
        return text

    def restore(self, text: str) -> str:
        if not self.enabled or not text:
            return text
        for tok, val in sorted(self._rev.items(), key=lambda kv: -len(kv[0])):
            text = text.replace(tok, val)
        return text

    @property
    def masked_count(self) -> int:
        return len(self._fwd)

    def summary(self) -> dict[str, int]:
        return dict(self._counts)
