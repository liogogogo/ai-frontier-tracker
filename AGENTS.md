# AGENTS.md — AI Frontier Tracker

本文件面向在本仓库中工作的自动化代理与人类协作者，概括入口与约定。**完整架构方案**（分层、数据流、扩展点、权衡）见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)。

## 项目在做什么

**为谁**：想从公开信号里**挖掘未来有潜力的 AI 项目与方向**（创始/研究/工程均可），并看清**当下大家都在往哪扎堆**的人。

**做什么**：汇总大厂官宣与工程博客、论文预印本、HN/Reddit/Lobsters 与 GitHub 等；排序与洞察（含词频/趋势 API）用于发现**早期技术—产品叙事**与升温主题，而非泛资讯列表。交付形态是 API + 静态前端，数据在你本地或服务端。

**边界**：工具只提供线索与代理指标（热度、词频等），**不构成**对任何项目成败或回报的预测；不保证覆盖每个细分领域；「最前沿」指上述来源上的高时效、高互动与规则加权信号。

## 技术栈

- **运行时**: Python 3.9+（仓库内常见 `.venv`）
- **Web**: FastAPI、Uvicorn；静态资源挂载在 `/static`
- **数据**: SQLModel + SQLAlchemy，默认 SQLite：`./data/ai_news.db`（可用环境变量 `DATABASE_URL` 覆盖）
- **HTTP**: httpx（及各 fetcher 中的客户端封装）

根目录 `requirements.txt` 列出直接依赖；部分功能（如中文周刊、`firecrawl`）可能还有可选依赖或 API Key，改相关 service 时再确认。

## 目录与模块

| 路径              | 作用                                                         |
| ----------------- | ------------------------------------------------------------ |
| `app/main.py`     | FastAPI 应用、`/api/*` 路由、静态挂载                        |
| `app/config.py`   | `AppConfig` / `CONFIG`，含各 fetcher 超时与 `schema_version` |
| `app/database.py` | 引擎、`init_db()`、SQLite 上对 `fetcher_states` 的轻量迁移   |
| `app/models.py`   | SQLModel 模型（文章、抓取器状态等）                          |
| `app/fetchers/`   | 可插拔数据源（注册表在 `FetcherRegistry`）                   |
| `app/services/`   | `collector`、缓存、调度、周刊摘要、Firecrawl 等              |
| `app/utils/`      | 热度、Bloom 去重、文本分析、ETag 等                          |
| `static/`         | 前端静态文件                                                 |
| `tests/`          | 手动/集成脚本（如 `tests/test_arxiv.py`）                    |
| `data/`           | 本地 SQLite 与数据文件（勿把敏感内容提交到公开仓库）         |

## 运行

使用说明、环境变量表与 API 列表见根目录 [README.md](./README.md)。最小启动：

```bash
pip install -r requirements.txt
cp .env.example .env   # 可选
uvicorn app.main:app --reload
```

按需设置 `DATABASE_URL` 与各 LLM / 外部 API 的 Key（详见 `.env.example` 与 `app/services/llm_summary.py`、`weekly_digest.py` 等）。容器部署见根目录 `Dockerfile` 与 `docker-compose.yml`、`README.md` 中「容器化」一节。

## 代理工作时的约定

1. **配置与版本**: 调整数据结构或 API 契约时，同步考虑 `CONFIG.schema_version`（`app/config.py`）及前端/缓存兼容性。
2. **新数据源**: 在 `app/fetchers/` 实现并挂到注册表；在 `CONFIG` 中为该源增加 `FetcherConfig` 便于调参。
3. **数据库**: `create_all` 不会给已有 SQLite 表自动加列；新增 ORM 字段时参考 `database.py` 中的迁移模式，避免旧库读行报错。
4. **范围**: 减少无关重构；与用户规则一致：改动应聚焦当前任务。
5. **忽略**: 不要编辑 `.venv`、体积大的二进制数据库除非任务明确要求。

## 相关 Cursor 配置

项目级细化规则可放在 `.cursor/rules/`（`.mdc` + frontmatter）。本文件负责仓库级鸟瞰；细则用规则文件补充。
