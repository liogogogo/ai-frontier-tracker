"""
同一实体在不同抓取源下的 URL 变体 → 规范形式，供列表去重与入库主键一致。
"""
import re
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


def canonicalize_link(url: str) -> str:
    """
    将论文 / 开源仓库 / 一般资讯 URL 规范为稳定形态，避免 abs 与 pdf、带版本号、
    带追踪参数等导致的重复条目。
    """
    if not url or not isinstance(url, str):
        return url or ""
    raw = url.strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if not parsed.netloc:
        return raw

    gh = _canonical_github_family(parsed)
    if gh:
        return gh

    ax = _canonical_arxiv(parsed)
    if ax:
        return ax

    return _strip_tracking_params(parsed)


def _canonical_github_family(parsed) -> Optional[str]:
    host = (parsed.netloc or "").lower().split(":")[0]
    if host == "github.com":
        return _github_repo_root(parsed)
    if host.endswith(".github.com") and host != "gist.github.com":
        return _github_repo_root(parsed)
    if host in ("gitlab.com", "www.gitlab.com"):
        return _gitlab_project_root(parsed)
    return None


def _path_segments(path: str) -> list:
    return [p for p in (path or "").split("/") if p]


def _github_repo_root(parsed) -> Optional[str]:
    parts = _path_segments(parsed.path)
    if len(parts) < 2:
        return None
    owner, repo = parts[0].lower(), parts[1].lower()
    if owner in ("settings", "marketplace", "sponsors"):
        return None
    repo = re.sub(r"\.git$", "", repo, flags=re.I)
    return f"https://github.com/{owner}/{repo}"


def _gitlab_project_root(parsed) -> Optional[str]:
    parts = _path_segments(parsed.path)
    if len(parts) < 2:
        return None
    return f"https://gitlab.com/{parts[0].lower()}/{parts[1].lower()}"


def _canonical_arxiv(parsed) -> Optional[str]:
    host = (parsed.netloc or "").lower()
    if "arxiv.org" not in host:
        return None
    path = parsed.path or ""
    aid: Optional[str] = None
    m = re.match(r"^/abs/([^?#]+)/?$", path, re.I)
    if m:
        aid = m.group(1).strip()
    else:
        m2 = re.match(r"^/pdf/([^?#]+?)(?:\.pdf)?/?$", path, re.I)
        if m2:
            aid = m2.group(1).strip()
    if not aid:
        return None
    aid = _arxiv_id_drop_version(aid)
    return f"https://arxiv.org/abs/{aid}"


def _arxiv_id_drop_version(arxiv_id: str) -> str:
    """
    新版 ID：2301.07041v3 → 2301.07041；旧版 cs/0012053v2 → cs/0012053（若带 v）。
    """
    s = arxiv_id.strip().strip("/")
    base = s.rsplit("/", 1)[-1]
    if re.match(r"^\d{4}\.\d{4,5}", base):
        return re.sub(r"v\d+$", "", base)
    if "/" in s:
        return re.sub(r"v\d+$", "", s)
    return re.sub(r"v\d+$", "", s)


_TRACK_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "source",
        "fbclid",
        "gclid",
    }
)


def _strip_tracking_params(parsed) -> str:
    scheme = (parsed.scheme or "https").lower()
    if scheme not in ("http", "https"):
        return urlunparse(parsed)
    host = (parsed.netloc or "").lower()
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    q = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in _TRACK_KEYS]
    query = urlencode(q) if q else ""
    return urlunparse((scheme, host, path, "", query, ""))
