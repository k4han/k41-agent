from langgraph.graph.state import CompiledStateGraph


class GraphRegistry:
    """
    Quản lý tất cả compiled graphs.
    Build 1 lần khi app start, reuse cho mọi request.
    """

    _graphs: dict[str, CompiledStateGraph] = {}

    @classmethod
    def register(cls, name: str, graph: CompiledStateGraph) -> None:
        cls._graphs[name] = graph
        print(f"[Registry] Registered graph: '{name}'")

    @classmethod
    def get(cls, name: str) -> CompiledStateGraph:
        if name not in cls._graphs:
            available = list(cls._graphs.keys())
            raise ValueError(
                f"Graph '{name}' not registered. "
                f"Available: {available}"
            )
        return cls._graphs[name]

    @classmethod
    def all(cls) -> dict[str, CompiledStateGraph]:
        return dict(cls._graphs)

    @classmethod
    def is_registered(cls, name: str) -> bool:
        return name in cls._graphs
