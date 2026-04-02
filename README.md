# AI Frontier Tracker

[![CI](https://github.com/liogogogo/ai-frontier-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/liogogogo/ai-frontier-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Self-hosted aggregator for the AI frontier** — 把**大模型相关的前沿论文、工程实践与社区讨论**收拢到一处，核心用途是辅助**挖掘未来有潜力的 AI 项目与方向**（早期技术/产品线索）；并配套**轻量分析**（词频、趋势、Feed 洞察），对齐**最活跃从业者**在公开渠道上押注什么、试什么。

数据来自大厂博客与 RSS、Hacker News、Reddit、Lobsters、arXiv、GitHub 等；经去重、热度与调度合并；可选 LLM 中文周评与 Firecrawl 正文增强。系统不代替你思考，但减少「漏掉主线」的概率。

---

## 为什么选择它

| 能力     | 说明                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------- |
| 前沿信号 | 论文 + 官宣 + 工程博客 + 极客社区，热量与渠道规则偏向**当下产业与工程前沿**                                         |
| 潜力线索 | 统一 Feed + `insights`；`/api/analytics/*` 做词频与**近期相对历史**的趋势对比，便于从噪声里筛**可能长成项目的方向** |
| 数据自控 | 默认 SQLite，可换 `DATABASE_URL`                                                                                    |
| 可扩展   | `app/fetchers/` 插件式数据源 + `FetcherRegistry`                                                                    |
| 可选 AI  | 无 API Key 仍可跑通 Feed；配置后支持周刊与摘要                                                                      |
| 轻量部署 | FastAPI + 静态前端，一条 `uvicorn` 或 Docker 即可                                                                   |

整体架构与数据流见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)；代理协作约定见 [AGENTS.md](./AGENTS.md)。

## 要求

- Python **3.9+**
- 依赖见 [requirements.txt](./requirements.txt)

## 快速开始

```bash
cd ai_news_search
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # 按需编辑
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

浏览器打开：<http://127.0.0.1:8000/>（`static/index.html`）

OpenAPI 文档：<http://127.0.0.1:8000/docs>

手动脚本在 [`tests/`](./tests/) 目录（例如验证 arXiv：`python tests/test_arxiv.py`，需在仓库根目录执行）。

## 容器化（Docker）

```bash
cp .env.example .env   # 可选；需要 LLM/Firecrawl 时再填 Key
docker compose up -d --build
```

默认 <http://127.0.0.1:8000/> ；SQLite 落在命名卷 `ai_news_data`（路径 `/app/data`）。修改端口：在 `.env` 里设置 `APP_PORT=8080`。

也可仅构建镜像后本地跑：

```bash
docker build -t ai-frontier-tracker .
docker run --rm -p 8000:8000 -v ai_news_data:/app/data ai-frontier-tracker
```

（`docker run` 需自行 `-e` 传入与 [.env.example](.env.example) 相同的环境变量；Compose 会从项目根 `.env` 注入。）

## 环境变量（可选）

复制 [.env.example](./.env.example)。核心变量：

| 变量                                      | 用途                                         |
| ----------------------------------------- | -------------------------------------------- |
| `DATABASE_URL`                            | 数据库（默认 `sqlite:///./data/ai_news.db`） |
| `OPENAI_API_KEY` / `OPENAI_MODEL`         | OpenAI 摘要与周刊                            |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`   | Claude                                       |
| `MODELVERSE_API_KEY` / `MODELVERSE_MODEL` | 兼容国内模型 API                             |
| `FIRECRAWL_API_KEY`                       | `/api/enhance`、`/api/scrape` 智能抓取       |

未配置 LLM / Firecrawl 时，对应接口会降级或返回提示，不影响主 Feed。

## API 一览

| 方法 | 路径                       | 说明                 |
| ---- | -------------------------- | -------------------- |
| GET  | `/api/health`              | 健康检查             |
| GET  | `/api/feed?refresh=`       | 聚合 Feed            |
| POST | `/api/feed/refresh`        | 强制刷新             |
| GET  | `/api/weekly-digest`       | 中文周评（可选 LLM） |
| GET  | `/api/stats`               | 库内统计             |
| GET  | `/api/analytics/word-freq` | 词频                 |
| GET  | `/api/analytics/trending`  | 趋势词               |
| POST | `/api/enhance`             | Firecrawl 增强条目   |
| GET  | `/api/scrape`              | 单 URL 抓取          |
| POST | `/api/summarize`           | LLM 摘要             |

## 社区与治理

- [贡献指南](CONTRIBUTING.md) — 环境、PR 约定、新数据源 checklist  
- [行为准则](CODE_OF_CONDUCT.md) — Contributor Covenant  
- [安全披露](SECURITY.md) — 请勿在公开 Issue 中提交漏洞  

Issue / PR 欢迎。要点：新数据源请走 `app/fetchers/` 并更新 `app/config.py` 中对应 `FetcherConfig`；变更 API 或存储时同步 `schema_version`。

## 许可

[MIT License](LICENSE)
