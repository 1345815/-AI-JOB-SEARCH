# 职位搜索配置（马育琪 - 2027届校招）

## 已安装的搜索工具（/scrape 优先使用）

`/scrape` 会先运行 `.agents/skills/*/SKILL.md` 里启用的 CLI 工具，包括 `linkedin-search` 和 `freehire-search`。中国本地招聘网站通常有登录和反爬限制，优先使用下面的 WebSearch `site:` 查询作为补充，不要试图绕过它们的登录或验证码。

## 搜索网站

- **BOSS直聘（zhipin.com）** - 应届生和社招岗位较多，交互活跃
- **智联招聘（zhaopin.com）** - 综合招聘平台
- **前程无忧（51job.com）** - 综合招聘平台，校招信息多
- **猎聘（liepin.com）** - 中高端岗位，也适合应届生
- **linkedin.com/jobs** - 外企和英文岗位（由 `linkedin-search` CLI 覆盖）
- **freehire.me** - 全球技术岗位聚合（由 `freehire-search` CLI 覆盖）
- **牛客网（nowcoder.com）** - 校招专用，笔试面经丰富
- **脉脉（maimai.cn）** - 职场社交，内推机会多

## 查询类别

### 优先级 1：核心岗位方向 — AI游戏策划

```
site:zhipin.com AI游戏策划 应届生 校招
site:zhipin.com 游戏策划 LLM Agent 校招
site:zhaopin.com AI游戏策划 应届 校招
site:51job.com AI游戏策划 应届生 校招
site:liepin.com AI游戏策划 应届生
site:linkedin.com/jobs "AI Game Designer" China
site:nowcoder.com AI游戏策划 校招
```

### 优先级 2：核心岗位方向 — AI产品运营

```
site:zhipin.com AI产品运营 应届生 校招
site:zhipin.com AI产品运营 商业化 营销 校招
site:zhaopin.com AI产品运营 应届 校招
site:51job.com AI产品运营 应届生 校招
site:liepin.com AI产品运营 应届生
site:linkedin.com/jobs "AI Product Operations" China
site:nowcoder.com AI产品运营 校招
```

### 优先级 3：技能关键词搜索

```
site:zhipin.com LLM Prompt工程 应届生 校招
site:zhipin.com 游戏系统拆解 商业化 应届生
site:zhaopin.com Python数据分析 AI产品 应届 校招
site:51job.com AI工具链 产品经理 应届生 校招
site:liepin.com AI产品 独立开发 应届生
site:linkedin.com/jobs "Prompt Engineer" China
```

### 优先级 4：相邻/可转型岗位

```
site:zhipin.com 游戏策划 应届生 校招
site:zhipin.com 产品经理 AI方向 应届生 校招
site:zhaopin.com 技术产品经理 应届 校招
site:51job.com 游戏运营 应届生 校招
site:zhipin.com 内容运营 游戏 应届生 校招
site:linkedin.com/jobs "Game Designer" China
site:linkedin.com/jobs "Product Manager" AI China
```

### 优先级 5：大厂校招专项

```
site:zhipin.com 网易 互娱 校招 应届生
site:zhipin.com 阿里巴巴 校招 应届生 AI
site:zhipin.com 腾讯 校招 应届生 游戏
site:zhipin.com 字节跳动 校招 应届生 AI
site:zhipin.com 米哈游 校招 应届生
site:nowcoder.com 网易互娱 校招
site:nowcoder.com 阿里 校招 AI
```

### 优先级 6：实习岗位

```
site:zhipin.com AI游戏策划 实习生
site:zhipin.com AI产品运营 实习生
site:zhaopin.com 游戏策划 实习
site:51job.com AI产品 实习生
site:liepin.com AI 实习
site:linkedin.com/jobs "AI Game" intern China
```

### 优先级 7：英文/外企岗位

```
site:linkedin.com/jobs "AI Product Manager" China
site:linkedin.com/jobs "Game Designer" China
site:linkedin.com/jobs "AI Operations" China
site:zhipin.com "AI Product" 应届生
bun run .agents/skills/freehire-search/cli/src/cli.ts search -q "AI Product" --region apac --format json
```

## 地点筛选

全国范围均可接受，优先关注：
- 一线城市（北京、上海、深圳、广州、杭州）— 优先
- 郑州及周边 — 可接受
- 成都、武汉、南京、苏州等新一线城市 — 可接受
- 全国其他城市 — 可接受

## 时间筛选

只保留 14 天内发布、或截止日期未过的职位；无法确定发布时间的标记为"时间未知"。

## 校招特殊规则

- 优先关注标题或描述含"应届生""校招""管培生""秋招""春招"的职位。
- 对明确注明"仅限社招"或要求多年经验的职位，直接排除（deal-breaker）。
- 大型企业校招通常有网申截止日期，务必在展示结果时标出。
- 关注牛客网校招信息，及时获取笔试/面试时间线。

## 自定义搜索

用户指定方向时（例如 `/scrape 游戏策划`），从对应类别挑选查询，并额外生成 2-3 条针对该方向的查询。
