"""EmysTester —— Emily Core 生产环境实战测试（向后兼容入口）。

新代码请从子模块导入：
    from .tester import EmysTester
    from .config_loader import get_core_url, get_llm_config, get_pg_config
    from .cli import main
"""
from __future__ import annotations

from tester import EmysTester
from config_loader import get_core_url, get_llm_config, get_pg_config, _read_cmd_config, _load_pg_config
from cli import main

if __name__ == "__main__":
    main()
