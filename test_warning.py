import asyncio
import warnings

async def test():
    import traceback
    
    original_warn = warnings.warn
    def warn_with_traceback(message, category=None, stacklevel=1, source=None):
        print(f"WARNING CAUGHT: {message}")
        traceback.print_stack()
        original_warn(message, category, stacklevel, source)
    
    warnings.warn = warn_with_traceback
    
    from langchain_core.messages import HumanMessage
    from agent.modules.workflows.public import make_run_config, make_run_context
    from agent.modules.workflows.infrastructure.langgraph.graphs.router import get_router_graph
    from langgraph.checkpoint.memory import MemorySaver

    graph = get_router_graph(MemorySaver())
    config = {"configurable": {"thread_id": "test_123"}}
    context = {"working_dir": "test", "max_context_tokens": 100, "agent_name": "x", "allowed_tool_names": []}
    
    try:
        async for event in graph.astream(
            {"messages": [HumanMessage(content="hi")]},
            config=config,
            context=context,
            stream_mode="values"
        ):
            print("EVENT RECVD")
    except Exception as e:
        print("ERROR:", e)

if __name__ == "__main__":
    asyncio.run(test())
