# agent/config.py

import os
from langchain_core.runnables import RunnableConfig


def make_config(
    thread_id:    str,
    service_type: str        = "default",
    working_dir:  str | None = None,
    recursion_limit: int     = 100,
) -> RunnableConfig:
    """
    Tạo config chuẩn cho mỗi request.
    working_dir và service_type đi qua configurable — không vào State.
    """
    return {
        "configurable": {
            "thread_id":    thread_id,
            "service_type": service_type,
            "working_dir":  working_dir or os.getcwd(),
        },
        "recursion_limit": recursion_limit,
    }
