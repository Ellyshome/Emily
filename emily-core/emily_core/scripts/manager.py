"""ScriptManager — scripts/ 目录的对外聚合层。

补三件事：统一调用入口、自描述、CLI/HTTP 测试接口。
不替代 scripts/ 目录本身，注册元信息仍走 emily-data/config/scripts_registry.yaml 的声明式清单。

与 ToolManager 的边界：ToolManager 管 LLM 运行时工具（BusinessFlowTool.handler，进程内 async）；
ScriptManager 管开发者/维护脚本（scripts/*.py，subprocess CLI）。
两者共享 service 层（node_batch / InsightGenerator 等），互不调用。
"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

from .script_entry import ScriptEntry
from .registry import ScriptRegistry

logger = logging.getLogger("emily.scripts.manager")

_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # manager.py → scripts/ → emily_core/ → emily-core/ → Emily/


class ScriptManager:
    """ScriptRegistry 的聚合层，提供统一调用/自描述/测试接口。

    仅管开发者/维护脚本。LLM 运行时工具见 ToolManager。
    """

    def __init__(self, registry: ScriptRegistry):
        self._registry = registry

    # ── 自描述 ────────────────────────────────────────

    def list(self) -> list[dict]:
        """列出所有脚本的元信息（轻量）。"""
        return [
            {
                "name": e.name,
                "category": e.category,
                "status": e.status,
                "writes_db": e.writes_db,
                "auto_run": e.auto_run,
                "has_check": e.has_check,
                "description": e.description,
            }
            for e in self._registry.entries
        ]

    def describe(self, name: str | None = None) -> dict:
        """单个或全部脚本的完整描述。

        Args:
            name: 指定脚本名；None 返回全部。
        Returns:
            name 非空: 完整 ScriptEntry 字典
            name 为空: {"scripts": [...], "count": N}
            脚本不存在: {"error": "...", "code": 2}
        """
        if name:
            e = self._registry.get(name)
            if not e:
                return {"error": f"script '{name}' not found", "code": 2}
            return self._entry_to_dict(e)
        scripts = [self._entry_to_dict(e) for e in self._registry.entries]
        return {"scripts": scripts, "count": len(scripts)}

    def export(self, format: str = "markdown") -> dict | str:
        """导出注册表信息。

        Args:
            format: "markdown" 生成 docs/脚本工具目录.md；"json" 返回 dict。
        """
        if format == "markdown":
            from .catalog import generate_markdown
            return generate_markdown(self._registry)
        return self.describe(None)

    # ── 统一调用 ──────────────────────────────────────

    def check(self, name: str | None = None) -> dict:
        """跑每个脚本的 check_arg 做就绪检查。

        Args:
            name: 指定脚本名；None 跑全部。
        Returns:
            {"results": [{name, category, ready, returncode, note}, ...], "count": N}
        """
        entries = [self._registry.get(name)] if name else self._registry.entries
        if name and entries[0] is None:
            return {"error": f"script '{name}' not found", "code": 2}

        results = []
        for e in entries:
            if e is None:
                continue
            if e.check_arg is None:
                results.append({
                    "name": e.name, "category": e.category,
                    "ready": "n/a", "returncode": None,
                    "note": "no check_arg defined",
                })
                continue
            result = self.run(e.name, args=[e.check_arg], timeout=e.timeout_seconds)
            ready = result.get("returncode") in (0, 1, None) if result.get("success") else False
            results.append({
                "name": e.name, "category": e.category,
                "ready": ready,
                "returncode": result.get("returncode"),
                "note": result.get("stdout", "").strip()[:200] if result.get("success") else result.get("stderr", "").strip()[:200],
            })

        return {"results": results, "count": len(results)}

    def run(self, name: str, args: list[str] | None = None, timeout: int | None = None) -> dict:
        """执行脚本（subprocess 默认 + entrypoint 可选 in-process）。

        Returns:
            成功: {"success": True, "returncode": 0, "stdout": "...", "stderr": "...", "script": name, "code": 0}
            不存在: {"success": False, "error": "...", "script": name, "code": 2}
            执行失败: {"success": False, "error": "...", "returncode": N, "stderr": "...", "script": name, "code": 1}
        """
        e = self._registry.get(name)
        if not e:
            return {"success": False, "error": f"script '{name}' not found",
                    "script": name, "code": 2}

        args = args or []
        timeout = timeout or e.timeout_seconds

        # entrypoint in-process 快路径
        if e.entrypoint:
            return self._run_inprocess(e, args, timeout)

        # subprocess 执行
        script_path = _PROJECT_ROOT / e.source_path
        if not script_path.exists():
            return {"success": False, "error": f"script file not found: {script_path}",
                    "script": name, "returncode": -1, "code": 1}

        try:
            proc = subprocess.run(
                [sys.executable, str(script_path), *args],
                capture_output=True, text=True, timeout=timeout,
            )
            return {
                "success": proc.returncode == 0,
                "returncode": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
                "script": name,
                "code": 0 if proc.returncode == 0 else 1,
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"timeout after {timeout}s",
                    "script": name, "returncode": -1, "code": 1}
        except Exception as ex:
            logger.warning("scriptmgr run '%s' failed: %s", name, ex)
            return {"success": False, "error": str(ex),
                    "script": name, "returncode": -1, "code": 1}

    def _run_inprocess(self, entry: ScriptEntry, args: list[str], timeout: int) -> dict:
        """In-process 执行（entrypoint）"""
        try:
            mod_name, func_name = entry.entrypoint.split(":")
            import importlib
            import contextlib
            import io
            mod = importlib.import_module(mod_name)
            func = getattr(mod, func_name)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                result = func(args)
            stdout = buf.getvalue()
            return {
                "success": True, "returncode": 0,
                "stdout": stdout, "stderr": "",
                "script": entry.name, "code": 0,
                "result": result,
            }
        except Exception as ex:
            return {"success": False, "error": str(ex),
                    "script": entry.name, "returncode": -1, "code": 1}

    # ── smoke 测试 ────────────────────────────────────

    def test(self, name: str | None = None) -> dict:
        """跑 smoke 用例。"""
        import yaml
        cases_path = _PROJECT_ROOT / "emily-core" / "tests" / "scriptmgr_cases.yaml"
        cases: dict = {}
        if cases_path.exists():
            try:
                with open(cases_path, "r", encoding="utf-8") as f:
                    raw = yaml.safe_load(f)
                if isinstance(raw, dict):
                    cases = raw
            except Exception as ex:
                logger.warning("Failed to load scriptmgr_cases.yaml: %s", ex)

        if not cases:
            # 无 case 退化为 check
            return self.check(name)

        target_names = [name] if name else list(cases.keys())
        results = []
        passed = 0
        failed = 0

        for tname in target_names:
            if tname not in cases:
                results.append({"name": tname, "status": "skipped", "error": "no test case defined"})
                continue
            case = cases[tname]
            if not isinstance(case, dict):
                results.append({"name": tname, "status": "skipped", "error": "invalid case format"})
                continue
            test_args = case.get("args", [])
            expect_rc = case.get("expect_returncode", [0])
            try:
                entry = self._registry.get(tname)
                if not entry:
                    results.append({"name": tname, "status": "skipped", "error": "script not in registry"})
                    continue
                r = self.run(tname, args=test_args)
                rc_match = r.get("returncode") in expect_rc
                stdout_contains = case.get("expect_stdout_contains", "")
                stdout_ok = True
                if stdout_contains:
                    stdout_ok = stdout_contains in (r.get("stdout") or "")
                if rc_match and stdout_ok:
                    passed += 1
                    results.append({"name": tname, "status": "passed", "returncode": r.get("returncode"),
                                    "stdout_truncated": (r.get("stdout") or "")[:200]})
                else:
                    failed += 1
                    details = []
                    if not rc_match:
                        details.append(f"returncode {r.get('returncode')} not in {expect_rc}")
                    if not stdout_ok:
                        details.append(f"stdout does not contain '{stdout_contains}'")
                    results.append({"name": tname, "status": "failed", "error": "; ".join(details),
                                    "returncode": r.get("returncode")})
            except Exception as ex:
                failed += 1
                results.append({"name": tname, "status": "failed", "error": str(ex)})

        return {"results": results, "passed": passed, "failed": failed, "count": len(results)}

    # ── 内部 ──────────────────────────────────────────

    @staticmethod
    def _entry_to_dict(e: ScriptEntry) -> dict:
        return {
            "name": e.name,
            "description": e.description,
            "category": e.category,
            "source_path": e.source_path,
            "invocation": e.invocation,
            "check_arg": e.check_arg,
            "run_args": e.run_args,
            "auto_run": e.auto_run,
            "auto_run_args": e.auto_run_args,
            "writes_db": e.writes_db,
            "aggregation_parent": e.aggregation_parent,
            "status": e.status,
            "entrypoint": e.entrypoint,
            "timeout_seconds": e.timeout_seconds,
            "has_check": e.has_check,
        }
