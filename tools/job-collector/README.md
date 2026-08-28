# CareerPilot 本地岗位采集器（多平台）

用你本地 Chrome（自己的登录态）串行采集招聘平台岗位，导入 CareerPilot 岗位库。
设计原则与 BossHunter 一致：**检测到验证码、登录墙、频率限制或未知页面结构时安全停止，绝不尝试绕过**。

## 能力边界

| 平台 | 采集 | 导入后 |
|---|---|---|
| BOSS 直聘 | ✅ 完整适配（搜索→翻页→去重） | 自动评分/生成材料/跟踪投递 |
| 智联招聘 | ✅ 只读采集（骨架，需按 DOM 校准） | 同上 |
| 前程无忧 51job | ✅ 只读采集（骨架，需按 DOM 校准） | 同上 |

> 三平台严格串行，每个请求间随机延时（4-8 秒），页数默认 ≤3。

## 快速开始

```bash
# 1. 准备环境（一次性）
C:/Users/hp/.workbuddy/binaries/python/envs/collector/Scripts/pip install playwright

# 2. 采集（会打开本地 Chrome，先登录对应平台）
cd tools/job-collector
C:/Users/hp/.workbuddy/binaries/python/envs/collector/Scripts/python collector.py --platform boss --keyword "AI产品" --pages 3

# 3. 导入 CareerPilot
C:/Users/hp/.workbuddy/binaries/python/envs/collector/Scripts/python import_jobs.py --file jobs/xxx.json --base http://111.230.228.15:8000 --user 你的用户名 --password 你的密码
```

## 安全机制

- **验证码**：URL/页面出现"验证码/captcha/geetest" → 立即停止
- **登录墙**：出现"请登录/扫码登录" → 停止并提示先登录
- **频率限制**："访问过于频繁/频率限制" → 停止
- **未知结构**：找不到卡片选择器 → 停止，提示校准选择器
- 登录态保存在 `.browser-data/`，登录一次后续复用

## 校准新平台/新结构

1. 浏览器打开平台搜索页 → F12 检查岗位卡片 DOM
2. 修改 `adapters/zhilian.py` / `adapters/job51.py` 的 `card_selector` 与 `selectors`
3. 重新运行采集

## 导入接口

`POST /api/jobs/import`（登录态）：批量岗位数组 → 服务端去重（URL normalize）→ AI 评分 → 入库。
单次上限 200 条。
