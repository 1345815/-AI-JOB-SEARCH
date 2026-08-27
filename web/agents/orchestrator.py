"""CareerPilot 多 Agent 编排器。"""

from .builtin import builtin_agent
from .protocol import AgentResult
from .queue import AgentQueue


class CareerPilotOrchestrator:
    """按名称编排内置 Agent，并保留人工审批边界。"""

    def __init__(self, max_workers=4, handlers=None):
        self.queue = AgentQueue(max_workers)
        self.handlers = dict(handlers or {})

    def _run_one(self, name, context):
        handler = self.handlers.get(name)
        try:
            result = handler(context) if handler else builtin_agent(name, context)
            if isinstance(result, AgentResult):
                return result.as_dict()
            return AgentResult(name, data=result if isinstance(result, dict) else {"value": result}).as_dict()
        except Exception as exc:
            return AgentResult(name, status="error", error=str(exc)).as_dict()

    def execute(self, agents, context=None):
        agents = list(agents or [])
        context = context or {}
        if "human-approval" in agents:
            return {"status": "approval_required", "results": [
                self._run_one("human-approval", context)]}
        return {"status": "ok", "results": [self._run_one(name, context) for name in agents]}

    def execute_async(self, agents, context=None, timeout=None):
        agents = list(agents or [])
        context = context or {}
        if "human-approval" in agents:
            return self.execute(agents, context)
        results = self.queue.run([lambda name=name: self._run_one(name, context) for name in agents], timeout)
        return {"status": "ok", "results": results}
