from typing import NotRequired

from agent.modules.workflows.state.base import BaseState


class AcpState(BaseState):
    """State of an ACP agent thread. Holds primitives only, so it checkpoints cleanly."""

    acp_session_id: NotRequired[str]
    acp_session_cwd: NotRequired[str]
    acp_turn_id: NotRequired[str]
    acp_boot_id: NotRequired[str]
    acp_pending: NotRequired[bool]
