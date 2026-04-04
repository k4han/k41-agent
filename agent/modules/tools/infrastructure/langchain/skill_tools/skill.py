from langchain_core.tools import tool

from agent.modules.skills.public import get_skill_content_xml


@tool
def skill(name: str) -> str:
    """
    LLM có thể gọi tool này để sử dụng các skill đã được đăng ký trong hệ thống.
    """
    content_xml = get_skill_content_xml(name)
    if content_xml is None:
        return "[error] skill not found"
    return content_xml
