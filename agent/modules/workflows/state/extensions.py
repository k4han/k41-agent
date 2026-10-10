from typing import Optional

from agent.modules.workflows.state.base import BaseState


class CodingState(BaseState):
    """
    State cho coding agent.
    working_dir vẫn đi qua config, không cần thêm field.
    Thêm field nếu cần lưu trung gian trong quá trình xử lý.
    """

    pass


class ResearchState(BaseState):
    """State for research agent — can store intermediate sources and summary."""

    sources: Optional[list[str]] = None
    summary: Optional[str] = None


class DecisionAgentState(BaseState):
    """State for decision-guided context curation agent."""

    directory_tree: Optional[str] = None
    candidate_files: Optional[list[str]] = None
    included_files: Optional[list[dict]] = None
    curated_context: Optional[str] = None
