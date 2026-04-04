from langgraph.graph import MessagesState


class BaseState(MessagesState):
    """
    Base state cho tất cả graphs.
    Kế thừa MessagesState (đã có trường messages: list[BaseMessage]).
    Các thông tin như working_dir đi qua Runtime[WorkflowContext]
    (context_schema) — không nằm ở đây.
    """

    pass
