"""Agent 输入输出协议。"""

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class AgentResult:
    agent: str
    status: str = "ok"
    data: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def as_dict(self):
        return {"agent": self.agent, "status": self.status,
                "data": self.data, "error": self.error}
