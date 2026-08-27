"""Agent 注册表，保持业务编排与具体 Agent 解耦。"""


class AgentRegistry:
    def __init__(self):
        self._agents = {}

    def register(self, name, handler):
        if not name or not callable(handler):
            raise ValueError("agent name and callable handler are required")
        self._agents[name] = handler
        return handler

    def get(self, name):
        return self._agents.get(name)

    def names(self):
        return tuple(self._agents)
