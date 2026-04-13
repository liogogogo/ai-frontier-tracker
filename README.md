# AI Frontier Tracker

[![CI](https://github.com/liogogogo/ai-frontier-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/liogogogo/ai-frontier-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Self-hosted aggregator for the AI frontier** — 把**大模型相关的前沿论文、工程实践与社区讨论**收拢到一处，核心用途是辅助**挖掘未来有潜力的 AI 项目与方向**（早期技术/产品线索）；并配套**轻量分析**（词频、趋势、Feed 洞察），对齐**最活跃从业者**在公开渠道上押注什么、试什么。

数据来自大厂博客与 RSS、**HuggingFace Papers**、Hacker News、Reddit、Lobsters、arXiv、GitHub 等；经去重、热度与调度合并；配套**词频分析、趋势检测、Topic Cards、行业影响分布**等分析接口；可选 LLM 中文周评与 Firecrawl 正文增强。系统不代替你思考，但减少「漏掉主线」的概率。

---

## 为什么选择它

| 能力     | 说明                                                                                                                |
| -------- | ------------------------------------------------------------------------------------------------------------------- |
| 前沿信号 | 论文 + 官宣 + 工程博客 + 极客社区（含 HuggingFace Papers upvotes），热量与渠道规则偏向**当下产业与工程前沿** |
| 潜力线索 | `/api/analytics/trending`（G² 趋势）、`/api/analytics/topics`（跨源 Topic Cards）、`/api/analytics/industry`（AI Agent 工程行业影响分布） |
| 多维分析 | 词频（TF-IDF × 热度 × 时效）、PMI 短语、趋势、主题卡片（含三类证据回溯）、行业影响分布（可选 LLM 打分增强） |
| 数据自控 | 默认 SQLite，可换 `DATABASE_URL`；评分结果增量缓存于 `Article.raw_data`，中断可续 |
| 可扩展   | `app/fetchers/` 插件式数据源 + `FetcherRegistry`；`app/services/industry_scorer.py` 内置 taxonomy，新增行业只需追加子领域 |
| 可选 AI  | 无 API Key 仍可跑通全部 Feed 与规则分析；配置 LLM Key 后支持周刊、摘要、行业评分增强 |
| 轻量部署 | FastAPI + 静态前端，一条 `uvicorn` 或 Docker 即可 |

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
uvicorn app.main:app --reload --host 0.0.0.0 --port 8767
```

浏览器打开：<http://127.0.0.1:8767/>（`static/index.html`）

OpenAPI 文档：<http://127.0.0.1:8767/docs>

手动脚本在 [`tests/`](./tests/) 目录（例如验证 arXiv：`python tests/test_arxiv.py`，需在仓库根目录执行）。

## 容器化（Docker）

```bash
cp .env.example .env   # 可选；需要 LLM/Firecrawl 时再填 Key
docker compose up -d --build
```

默认将容器内 `8000` 映射到主机 **8767**，访问 <http://127.0.0.1:8767/> ；SQLite 落在命名卷 `ai_news_data`（路径 `/app/data`）。若需改用其他主机端口：在 `.env` 里设置 `APP_PORT=9000`（示例）。

也可仅构建镜像后本地跑：

```bash
docker build -t ai-frontier-tracker .
docker run --rm -p 8767:8000 -v ai_news_data:/app/data ai-frontier-tracker
```

（`docker run` 需自行 `-e` 传入与 [.env.example](.env.example) 相同的环境变量；Compose 会从项目根 `.env` 注入。）

## 环境变量（可选）

复制 [.env.example](./.env.example)。核心变量：

| 变量                                      | 用途                                         |
| ----------------------------------------- | -------------------------------------------- |
| `FEED_CACHE_TTL_SECONDS`                  | 内存 Feed 缓存秒数（默认 `1800`≈30 分钟；过期后 `GET /api/feed` 会重新抓取） |
| `DATABASE_URL`                            | 数据库（默认 `sqlite:///./data/ai_news.db`） |
| `OPENAI_API_KEY` / `OPENAI_MODEL`         | OpenAI 摘要与周刊                            |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`   | Claude                                       |
| `MODELVERSE_API_KEY` / `MODELVERSE_MODEL` | 兼容国内模型 API                             |
| `FIRECRAWL_API_KEY`                       | `/api/enhance`、`/api/scrape` 智能抓取       |

未配置 LLM / Firecrawl 时，对应接口会降级或返回提示，不影响主 Feed。

## API 一览

### 核心 Feed

| 方法 | 路径                 | 说明                              |
| ---- | -------------------- | --------------------------------- |
| GET  | `/api/health`        | 健康检查                          |
| GET  | `/api/health/detailed` | 详细健康状态（含各 fetcher 状态） |
| GET  | `/api/feed`          | 聚合 Feed（`?refresh=true` 强刷） |
| POST | `/api/feed/refresh`  | 强制后台刷新                      |
| GET  | `/api/weekly-digest` | 中文周评（可选 LLM）              |
| GET  | `/api/stats`         | 库内文章统计                      |

### 分析接口

| 方法 | 路径                           | 说明                                                             | 关键参数 |
| ---- | ------------------------------ | ---------------------------------------------------------------- | -------- |
| GET  | `/api/analytics/word-freq`     | TF-IDF × 热度 × 时效词频                                        | `days`, `top_k`, `article_type` |
| GET  | `/api/analytics/trending`      | G² 趋势词（近期 vs 历史对比）                                    | `recent_days`, `compare_days`, `top_k` |
| GET  | `/api/analytics/topics`        | **Topic Cards**：paper/repo/news 三类分别提名，跨源一致性重排，含证据回溯 | `recent_days`, `compare_days`, `top_k`, `evidence_k` |
| GET  | `/api/analytics/industry`      | **AI Agent 工程行业影响分布**：10 个子领域评分 + 分布 + top evidence；规则打分始终可用，`use_llm=true` 时 LLM 增强 | `days`, `min_score`, `use_llm`, `force_rescore` |
| GET  | `/api/analytics/entities`      | **命名实体聚合**：模型/技术/工具/机构/Benchmark 的词频 + velocity + 跨源分，替代纯 token 词频 | `recent_days`, `compare_days`, `top_k`, `category` |
| GET  | `/api/analytics/convergence`   | **收敛信号卡**：同一 arXiv ID 被 paper+repo+news 三类来源同时覆盖，论文→实现→讨论全链路高置信信号 | `days`, `min_source_types`, `top_k` |
| GET  | `/api/analytics/emergence`     | **涌现实体检测**：近期首次出现或加速度 >2× 的实体，早期信号探测 | `recent_days`, `compare_days`, `top_k`, `min_mentions` |
| GET  | `/api/analytics/paper-struct`  | **论文结构化抽取**（可选 LLM）：problem/method/key_metric/impl_url/novelty/one_liner，增量缓存 | `days`, `use_llm`, `force_reextract`, `limit` |

### 内容增强

| 方法 | 路径             | 说明                       |
| ---- | ---------------- | -------------------------- |
| POST | `/api/enhance`   | Firecrawl 增强单条内容     |
| GET  | `/api/scrape`    | 抓取任意 URL（Firecrawl）  |
| POST | `/api/summarize` | LLM 摘要生成               |

## 社区与治理

- [贡献指南](CONTRIBUTING.md) — 环境、PR 约定、新数据源 checklist  
- [行为准则](CODE_OF_CONDUCT.md) — Contributor Covenant  
- [安全披露](SECURITY.md) — 请勿在公开 Issue 中提交漏洞  

Issue / PR 欢迎。要点：新数据源请走 `app/fetchers/` 并更新 `app/config.py` 中对应 `FetcherConfig`；变更 API 或存储时同步 `schema_version`。

## 许可

[MIT License](LICENSE)
