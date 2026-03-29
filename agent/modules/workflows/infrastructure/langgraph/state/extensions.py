from typing import Optional

from agent.modules.workflows.infrastructure.langgraph.state.base import BaseState


class CodingState(BaseState):
    """
    State cho coding agent.
    working_dir vẫn đi qua config, không cần thêm field.
    Thêm field nếu cần lưu trung gian trong quá trình xử lý.
    """

    pass


class ResearchState(BaseState):
    """State cho research agent — có thể lưu sources, summary trung gian."""

    sources: Optional[list[str]] = None
    summary: Optional[str] = None
