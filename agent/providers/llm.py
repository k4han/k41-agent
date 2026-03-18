# agent/providers/llm.py

from functools import lru_cache
from langchain_openai import ChatOpenAI


@lru_cache(maxsize=None)
def get_llm(model: str = "devstral-2512", temperature: float = 0):
    """
    Cache LLM instance theo model + temperature.
    Tránh khởi tạo lại mỗi request.
    """
    return ChatOpenAI(
            model=model,
            base_url="https://api.mistral.ai/v1",
            api_key= "ftOH2SLPQk4B4qoj9owxRCeJOuORBueq",
            temperature=temperature
        )
