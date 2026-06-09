from __future__ import annotations

import os
import subprocess
from typing import Any


def hidden_subprocess_kwargs(*, creationflags: int = 0) -> dict[str, Any]:
    if os.name != "nt":
        return {}

    kwargs: dict[str, Any] = {
        "creationflags": creationflags | getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }
    startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_factory is None:
        return kwargs

    startupinfo = startupinfo_factory()
    startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 0)
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    kwargs["startupinfo"] = startupinfo
    return kwargs
