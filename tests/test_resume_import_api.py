import os

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("CAREERPILOT_TEST_URL"),
    reason="设置 CAREERPILOT_TEST_URL 后运行集成测试",
)


def test_live_api_smoke():
    import urllib.request

    url = os.environ["CAREERPILOT_TEST_URL"].rstrip("/")
    with urllib.request.urlopen(url + "/api/health") as resp:
        assert resp.status == 200
