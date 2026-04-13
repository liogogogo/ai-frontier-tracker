# AGENTS.md — AI Frontier Tracker

本文件面向在本仓库中工作的自动化代理与人类协作者，概括入口与约定。**完整架构方案**见 [docs/ARCHITECTURE.md](./docs/ARCHITECTURE.md)；**开源贡献与安全披露**分别见 [CONTRIBUTING.md](./CONTRIBUTING.md)、[SECURITY.md](./SECURITY.md)。

## 项目在做什么

**为谁**：想从公开信号里**挖掘未来有潜力的 AI 项目与方向**（创始/研究/工程均可），并看清**当下大家都在往哪扎堆**的人。

**做什么**：汇总大厂官宣与工程博客、论文预印本（含 HuggingFace Papers 社区热榜）、HN/Reddit/Lobsters 与 GitHub 等；排序与洞察（含词频/趋势/Topic Cards/行业影响分布 API）用于发现**早期技术—产品叙事**与升温主题，而非泛资讯列表。交付形态是 API + 静态前端，数据在你本地或服务端。

**边界**：工具只提供线索与代理指标（热度、词频等），**不构成**对任何项目成败或回报的预测；不保证覆盖每个细分领域；「最前沿」指上述来源上的高时效、高互动与规则加权信号。

## 技术栈

- **运行时**: Python 3.9+（仓库内常见 `.venv`）
- **Web**: FastAPI、Uvicorn；静态资源挂载在 `/static`
- **数据**: SQLModel + SQLAlchemy，默认 SQLite：`./data/ai_news.db`（可用环境变量 `DATABASE_URL` 覆盖）
- **HTTP**: httpx（及各 fetcher 中的客户端封装）

根目录 `requirements.txt` 列出直接依赖；部分功能（如中文周刊、`firecrawl`）可能还有可选依赖或 API Key，改相关 service 时再确认。

## 目录与模块

| 路径                                      | 作用                                                                       |
| ----------------------------------------- | -------------------------------------------------------------------------- |
| `app/main.py`                             | FastAPI 应用、`/api/*` 路由、静态挂载                                      |
| `app/config.py`                           | `AppConfig` / `CONFIG`，含各 fetcher 超时与 `schema_version`               |
| `app/database.py`                         | 引擎、`init_db()`、SQLite 上对 `fetcher_states` 的轻量迁移                 |
| `app/models.py`                           | SQLModel 模型（文章、抓取器状态等）                                        |
| `app/fetchers/`                           | 可插拔数据源（注册表在 `FetcherRegistry`）                                 |
| `app/fetchers/huggingface.py`             | HuggingFace Papers 社区热榜抓取器（upvotes 作为社交热度信号）              |
| `app/services/`                           | `collector`、缓存、调度、周刊摘要、Firecrawl 等                            |
| `app/services/industry_scorer.py`         | AI Agent 工程行业评分服务：10 子领域 taxonomy + 规则打分 + LLM 增强 + 聚合 |
| `app/utils/`                              | 热度、Bloom 去重、文本分析、ETag、URL 规范化等                             |
| `app/utils/text_analysis.py`              | TF-IDF × 热度 × 时效词频、PMI 短语、G² 趋势、Topic Cards（跨源一致性重排）|
| `app/utils/dedup_urls.py`                 | URL 规范化（arXiv abs/pdf、GitHub 子路径、追踪参数剥离）                   |
| `app/utils/entity_dict.py`               | AI 领域命名实体字典（~150 实体，5 类型），`extract_entities(text)` 返回 id 列表 |
| `app/services/signal_analytics.py`       | 实体聚合+动量、收敛信号卡（arXiv 三角关联）、涌现检测；纯计算，无外部依赖     |
| `app/services/paper_extractor.py`        | 论文摘要结构化抽取（可选 LLM）；problem/method/key_metric/novelty；增量缓存   |
| `static/`                                 | 前端静态文件                                                               |
| `tests/`                                  | 手动/集成脚本（如 `tests/test_arxiv.py`）                                  |
| `data/`                                   | 本地 SQLite 与数据文件（勿把敏感内容提交到公开仓库）                       |

## 运行

使用说明、环境变量表与 API 列表见根目录 [README.md](./README.md)。最小启动：

```bash
pip install -r requirements.txt
cp .env.example .env   # 可选
uvicorn app.main:app --reload --port 8767
```

按需设置 `DATABASE_URL` 与各 LLM / 外部 API 的 Key（详见 `.env.example` 与 `app/services/llm_summary.py`、`weekly_digest.py` 等）。容器部署见根目录 `Dockerfile` 与 `docker-compose.yml`、`README.md` 中「容器化」一节。

## 代理工作时的约定

1. **配置与版本**: 调整数据结构或 API 契约时，同步考虑 `CONFIG.schema_version`（`app/config.py`）及前端/缓存兼容性。
2. **新数据源**: 在 `app/fetchers/` 实现并挂到注册表；在 `CONFIG` 中为该源增加 `FetcherConfig` 便于调参；`venue` 字段要与 `heat.py` 的渠道逻辑对齐（`huggingface:*` 前缀由 `finalize_heat` 特殊处理）。
3. **数据库**: `create_all` 不会给已有 SQLite 表自动加列；新增 ORM 字段时参考 `database.py` 中的迁移模式，避免旧库读行报错。评分结果写入 `Article.raw_data`（JSON 合并），不新增列。
4. **分析接口**: 新增分析维度优先在 `app/utils/text_analysis.py`（统计方法）或 `app/services/industry_scorer.py`（评分/taxonomy）扩展；不要在路由层堆计算逻辑。
5. **行业 taxonomy 扩展**: 追加行业/子领域只需在 `industry_scorer.py` 的 `AGENT_TAXONOMY` 列表里新增 `SubDomain`；不影响现有评分缓存（旧缓存照常使用，`force_rescore=true` 触发重算）。
6. **arXiv 限流**: `rate_limit_delay=3.25s`（≥3s/次是官方建议）；`max_retries=8`；429 冷却 28s。不要把这个值调低，否则会持续触发 429。
7. **实体补填**: 新增或修改 `entity_dict.py` 后，已入库的历史文章不会自动更新 `entities`。调用 `POST /api/admin/backfill-entities?days=90` 触发补填；`force=true` 强制覆盖已有结果。
8. **raw_data 写入规范**: 所有向 `Article.raw_data` 写入的代码必须使用 `DatabaseCache._merge_raw_data(existing, updates)` 合并，禁止直接赋值 `art.raw_data = json.dumps(...)` 覆盖（会破坏其他模块的缓存字段）。
9. **收敛信号**: `/api/analytics/convergence` 依赖 arXiv ID 在不同来源文章的 `raw_data['arxiv_id']` 中同时出现；目前仅从 `link` 字段自动提取，HN/Reddit/GitHub 讨论通常无 arXiv 链接，实际命中率较低。
10. **范围**: 减少无关重构；与用户规则一致：改动应聚焦当前任务。
11. **忽略**: 不要编辑 `.venv`、体积大的二进制数据库除非任务明确要求。

## 参考范式：`karpathy/jobs` 的“可复现评分流水线”

`karpathy/jobs`（US Job Market Visualizer）提供了一个非常实用的工程范式：用**权威、覆盖全集的数据底座** + **统一 rubric 的 LLM 结构化打分**，把“模糊问题”变成可聚合、可视化、可复跑的量化图层。

可迁移的精髓要点：

- **全集覆盖的底座数据（Ground-truth corpus）**：先确保对象集合完整且可比较（在 jobs 里是 342 个职业；在本仓库可对应「一组行业/领域 taxonomy」或「一组主题集合」）。
- **统一口径的评分 rubric（One prompt to rule them all）**：对每个对象用同一个评分标准输出结构化 JSON（例如 `score: 0-10` + `rationale`），保证可比性与可解释性。
- **强约束结构化输出（JSON-only）**：要求模型只输出固定 JSON schema，避免文本漂移，便于后续聚合与回溯。
- **增量缓存 + 可续跑（Checkpointing）**：逐条写入结果（如 `scores.json`），可中断恢复，避免长跑失败重来。
- **聚合成“可视化友好”的紧凑数据（Build site data）**：将结构化字段（例如就业人数/热度/时间）与 LLM 评分合并成一个小型 `data.json`，供前端直接渲染与交互切换图层。
- **分布与证据回溯并重（Distribution + evidence）**：不仅给总体分布，还能回溯每个 bucket/主题对应的代表性样本（标题/链接/摘要），形成可审计的洞察。

在本仓库的落地实现（已完成）：

- **`/api/analytics/industry`**（`app/services/industry_scorer.py`）：AI Agent 工程行业，10 个子领域 taxonomy，规则打分（始终可用）+ 可选 LLM 增强 + 增量缓存写入 `Article.raw_data` + 聚合分布与 top evidence。
- **`/api/analytics/topics`**（`app/utils/text_analysis.py` `build_topic_cards()`）：paper/repo/news 三类分别用 G² 趋势 + TF-IDF 提名，跨源一致性重排，每个主题含三类证据回溯。
- **扩展新行业**：在 `AGENT_TAXONOMY` 追加 `SubDomain`，或复制 `industry_scorer.py` 为新模块（如 `healthcare_scorer.py`），接口层加对应参数即可。

## 相关 Cursor 配置

项目级细化规则可放在 `.cursor/rules/`（`.mdc` + frontmatter）。本文件负责仓库级鸟瞰；细则用规则文件补充。
