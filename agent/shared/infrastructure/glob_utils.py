"""Path glob translation for sandbox images running Python 3.11 or 3.12."""

import fnmatch
import glob
import os
import re


def translate_path_glob(pattern: str) -> str:
    """Match recursive globs and hidden entries without crossing separators."""
    if hasattr(glob, "translate"):
        return glob.translate(pattern, recursive=True, include_hidden=True)
    separators = os.sep + (os.altsep or "")
    escaped = "".join(map(re.escape, separators))
    separator = f"[{escaped}]" if len(separators) > 1 else escaped
    non_separator = f"[^{escaped}]"
    parts = re.split(separator, pattern)
    expressions = []
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if part == "**":
            if last:
                expressions.append(".*")
            elif parts[index + 1] != "**":
                expressions.append(f"(?:.+{separator})?")
            continue
        if part == "*":
            expressions.append(non_separator + "+")
        else:
            # fnmatch handles classes, escaping and optimized star groups.
            # Replace wildcard dots outside classes to confine each segment.
            translated = fnmatch.translate(part)[4:-3]
            cursor = 0
            in_class = False
            class_start = -1
            while cursor < len(translated):
                character = translated[cursor]
                if character == "\\":
                    expressions.append(translated[cursor:cursor + 2])
                    cursor += 2
                    continue
                if character == "[" and not in_class:
                    in_class = True
                    class_start = cursor
                elif character == "]" and in_class:
                    first_content = class_start + (2 if translated[class_start + 1] == "^" else 1)
                    if cursor > first_content:
                        in_class = False
                if character == "." and not in_class:
                    expressions.append(non_separator)
                elif character == "^" and in_class and cursor == class_start + 1:
                    expressions.append("^" + escaped)
                else:
                    expressions.append(character)
                cursor += 1
        if not last:
            expressions.append(separator)
    return "(?s:" + "".join(expressions) + ")\\Z"
