# Contributing to AI Frontier Tracker

感谢你有兴趣参与。本文说明如何提交 Issue / PR，以及与代码结构相关的约定。

## 行为准则

参与本仓库即表示你同意遵守 [Contributor Covenant](CODE_OF_CONDUCT.md)。

## 安全问题

请勿在公开 Issue 中披露安全漏洞。请按 [SECURITY.md](SECURITY.md) 私下联系维护者。

## 开发环境

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install ruff           # 可选，本地静态检查
uvicorn app.main:app --reload --port 8767
```

复制 [.env.example](.env.example) 为 `.env` 并按需填写（本地运行不填也可跑通主 Feed）。

## 提交代码前建议

- `python -m compileall app tests`
- `python -c "from app.main import app"`
- `ruff check app tests`（若已安装 ruff；与 CI 语法/导入检查互补）
- 若修改了抓取或存储逻辑，可运行 `python tests/test_arxiv.py`（需网络）

## Pull Request 约定

1. **聚焦**：一个 PR 解决一类问题，避免无关重构。
2. **新数据源**：在 `app/fetchers/` 实现并 `@register_fetcher("name")`；在 `app/fetchers/__init__.py` 中 import；在 [app/config.py](app/config.py) 的 `AppConfig` 增加同名 `FetcherConfig` 字段（与 registry 名称一致）。
3. **API 或 JSON 契约变更**：递增 `CONFIG.schema_version`，并说明前端或消费方需同步。
4. **数据库**：SQLite 上新增列时，参考 [app/database.py](app/database.py) 中的迁移模式，避免旧库读行失败。
5. **不要提交**：`.env`、`data/*.db`、密钥、大型二进制；参见 [.gitignore](.gitignore)。

## 架构参考

设计级说明见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 许可

提交即表示你贡献的内容将在与项目相同的 [MIT License](LICENSE) 下分发。
