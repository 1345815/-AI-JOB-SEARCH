"""OpenAI 兼容 LLM 客户端（JSON 输出）。"""

import json
import os
import urllib.request
from pathlib import Path

SETTINGS_FILE = Path(__file__).resolve().parent / "data" / "settings.json"
LLM_TIMEOUT_SECONDS = int(os.environ.get("LLM_TIMEOUT_SECONDS", "35"))


def load_settings():
    settings = {
        "base_url": os.environ.get("LLM_API_BASE", ""),
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "model": os.environ.get("LLM_MODEL", ""),
        "enabled": os.environ.get("LLM_ENABLED", "") == "1",
    }
    if SETTINGS_FILE.exists():
        try:
            stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            for key in ("base_url", "api_key", "model", "enabled"):
                if key in stored:
                    settings[key] = stored[key]
        except Exception:
            pass
    return settings


def llm_available():
    s = load_settings()
    return bool(s.get("enabled") and s.get("api_key") and s.get("base_url"))


def _strip_json_fence(content):
    content = content.strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:]
    return content.strip()


def request_json(system, user_text):
    """调用 LLM 并返回解析后的 JSON dict；失败抛出 RuntimeError。"""
    s = load_settings()
    if not llm_available():
        raise RuntimeError("未配置 LLM")
    base = s["base_url"].rstrip("/")
    payload = {
        "model": s.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_text},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
        "response_format": {"type": "json_object"},
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + s["api_key"],
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    content = data["choices"][0]["message"]["content"]
    try:
        return json.loads(_strip_json_fence(content))
    except Exception as exc:
        raise RuntimeError(f"LLM 返回非 JSON：{content[:200]}") from exc
