## Summary

-

## Test plan

- [ ] `python -m compileall app tests`
- [ ] `python -c "from app.main import app"`
- [ ] (If applicable) `uvicorn app.main:app --reload --port 8767` runs and `/api/health` OK

## Checklist

- [ ] Focused change; no unrelated refactors
- [ ] If API/Feed shape changed: `schema_version` bumped and noted below
- [ ] If new fetcher: `CONFIG` + registry + `__init__.py` import updated

## Notes for reviewers

(Optional: trade-offs, follow-ups, screenshots)
