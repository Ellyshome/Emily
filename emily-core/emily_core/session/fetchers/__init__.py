"""Session 原子化能力子采集器包。

4 个独立 fetcher，每个可被 SessionDataFetcher 调用，也可独立运行。

独立运行方式：
  python -m emily_core.session.fetchers.fetch_available_tools --user-id <UUID>
  python -m emily_core.session.fetchers.fetch_visible_schema --user-id <UUID>
  python -m emily_core.session.fetchers.fetch_visible_files --user-id <UUID>
  python -m emily_core.session.fetchers.fetch_rag_info

注意：不在 __init__.py 中预导入子模块，避免 python -m 运行时产生 RuntimeWarning。
调用方按需 import，如：from .fetchers.fetch_available_tools import fetch
"""
