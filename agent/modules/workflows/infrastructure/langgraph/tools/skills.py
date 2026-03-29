from langchain_core.tools import tool

from agent.modules.skills.public import get_skill

@tool
def skill(name: str):
    """
    LLM có thể gọi tool này để sử dụng các skill đã được đăng ký trong hệ thống.
    """
    skill = get_skill(name)
    if not skill:
        return "[error] skill not found"
    return skill.body