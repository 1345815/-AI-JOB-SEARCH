# 每次 pytest 运行使用全新唯一 basetemp（系统临时目录）：
# Windows 沙箱对 rmtree 有批量删除保护（>50 文件需确认），且 \\?\ 长路径前缀
# 会使"系统临时目录豁免"判断失效，导致 session 开始清理旧 basetemp 时报错。
# 每次运行创建不存在的新目录 → 无需清理旧目录 → 稳定全绿。
import tempfile
import uuid
from pathlib import Path


def pytest_configure(config):
    config.option.basetemp = str(Path(tempfile.gettempdir()) / ("careerpilot-pytest-" + uuid.uuid4().hex[:8]))
