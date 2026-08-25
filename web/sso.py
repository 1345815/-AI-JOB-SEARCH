"""OIDC SSO 客户端抽象（纯标准库 urllib，默认关闭）。

配置（环境变量）：
- OIDC_ISSUER / OIDC_CLIENT_ID / OIDC_CLIENT_SECRET 未配置时本模块不启用，
  login 流程走原密码登录，SSO 入口不渲染。

用法：
    client = OIDCClient.from_env()
    if client:
        auth_url = client.authorize_url("https://app.example.com/callback")
        # 用户访问 auth_url → Provider 回调 code
        tokens = client.exchange_code(code, "https://app.example.com/callback")
        userinfo = client.userinfo(tokens["access_token"])
"""

import base64
import json
import os
import urllib.parse
import urllib.request


class OIDCClient(object):
    def __init__(self, issuer, client_id, client_secret, redirect_uri=""):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self._config = None

    @classmethod
    def from_env(cls):
        issuer = os.environ.get("OIDC_ISSUER", "").strip()
        cid = os.environ.get("OIDC_CLIENT_ID", "").strip()
        secret = os.environ.get("OIDC_CLIENT_SECRET", "").strip()
        if not (issuer and cid and secret):
            return None
        return cls(issuer, cid, secret, os.environ.get("OIDC_REDIRECT_URI", ""))

    def _get_config(self):
        if self._config is None:
            with urllib.request.urlopen(self.issuer + "/.well-known/openid-configuration", timeout=10) as resp:
                self._config = json.loads(resp.read().decode("utf-8"))
        return self._config

    def authorize_url(self, state="", scope="openid profile email"):
        cfg = self._get_config()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": scope,
            "state": state or "sso-state",
        }
        return cfg["authorization_endpoint"] + "?" + urllib.parse.urlencode(params)

    def exchange_code(self, code, redirect_uri=None):
        cfg = self._get_config()
        body = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri or self.redirect_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(cfg["token_endpoint"], data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def userinfo(self, access_token):
        cfg = self._get_config()
        req = urllib.request.Request(cfg["userinfo_endpoint"])
        req.add_header("Authorization", "Bearer " + access_token)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
