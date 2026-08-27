"""内置 Agent 的安全默认实现。

这些 Agent 只负责返回编排状态；真实简历、求职信和面试生成由现有业务 handler 执行。
"""

from .protocol import AgentResult


def builtin_agent(name, context):
    return AgentResult(name, data={"context": context or {}})
