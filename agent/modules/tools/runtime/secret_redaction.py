"""Streaming literal redaction before process output is retained or displayed."""

import re


class SecretRedactor:
    def __init__(self, values=()):
        variants = set()
        for value in values:
            if value:
                normalized = value.replace("\r\n", "\n")
                variants.update((value, normalized, normalized.replace("\n", "\r\n"), value.replace("\n", "\r\n")))
        self._values = tuple(sorted(variants, key=len, reverse=True))
        self._max_length = max(map(len, self._values), default=0)
        self._starts = re.compile("[" + re.escape("".join(sorted({value[0] for value in self._values}))) + "]") if self._values else None
        self._pending = ""

    def feed(self, text: str, *, final: bool = False) -> str:
        if not self._values:
            return text
        text = self._pending + text
        self._pending = ""
        output = []
        index = 0
        while index < len(text):
            start = self._starts.search(text, index)
            if start is None:
                output.append(text[index:])
                break
            output.append(text[index:start.start()])
            index = start.start()
            # A longer match may still arrive in the next pipe read.
            if not final and len(text) - index < self._max_length and any(
                                 value.startswith(text[index:]) and len(value) > len(text) - index
                                 for value in self._values):
                self._pending = text[index:]
                break
            match = next((value for value in self._values if text.startswith(value, index)), None)
            if match is not None:
                output.append("[REDACTED]")
                index += len(match)
            else:
                output.append(text[index])
                index += 1
        return "".join(output)
