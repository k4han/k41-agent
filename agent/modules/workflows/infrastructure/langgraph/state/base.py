from langgraph.graph import MessagesState


class BaseState(MessagesState):
    """
    Base state cho tất cả graphs.
    Kế thừa MessagesState (đã có trường messages: list[BaseMessage]).
    Các thông tin như working_dir, service_type đi qua config — không nằm ở đây.
    """

    pass
