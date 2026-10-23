"""library_fs —— 资料库文件仓储（Repository 层，全同步文件系统访问）。

职责边界（US-07 / 约束 1）：资料库是**独立于既有 RAG 检索源**的结构化资料通道，
本模块只做文件系统原语，不注册为任何检索源，也不引用 RAG 扫描目录解析。

  - 目录解析（多级回退）+ 越界防护（约束 13）
  - 列目录 / 读索引 / 读文本文件 / 写索引（写只落索引文件，不动原始资料 —— 约束 11）
  - 目录元信息（供索引时效判定，US-09）

本模块**不理解业务语义、不生成内容**；索引内容由入库引擎（M2）按手册渲染。
调用方（工具 / 脚本 / 控制台路由）均为异步壳，用 `asyncio.to_thread()` 包裹本层调用。
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("emily.knowledge_library.fs")

# 索引文件名 —— 全链路单点定义（约束 2）。其他模块一律 import 本常量，不写字面量。
INDEX_FILENAME = ".emily.md"

# 文本可读类型白名单（PRD 约束 3 待核实项：取值在此定）
TEXT_EXTENSIONS: frozenset[str] = frozenset({
    ".md", ".markdown", ".txt", ".text", ".json", ".csv", ".tsv",
    ".log", ".yml", ".yaml", ".ini", ".cfg", ".conf", ".sql", ".html", ".htm", ".xml",
})

# 已知不可按文本读取的类型（按扩展名分派，与 tools/parse_document_tool.py 同口径）：
# 图纸 / Office / 图片 / 压缩包 / 音视频 / 可执行与库文件等，一律不解析内容（AC-US-01.8）。
BINARY_EXTENSIONS: frozenset[str] = frozenset({
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".rtf",
    ".wps", ".et", ".dps", ".ofd",
    ".dwg", ".dxf", ".shp", ".dgn",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".heic", ".svg",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".m4a",
    ".eml", ".msg",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".dat", ".db", ".bak", ".pkl",
})

# 单次读取上限：超出即截断（避免把大文本灌进提示词预算）
DEFAULT_MAX_READ_BYTES = 200_000

# 无扩展名文件的内容嗅探窗口
_SNIFF_BYTES = 4096


class KnowledgeLibraryError(Exception):
    """资料库访问错误基类（调用方按需转成用户可读提示）。"""


class KnowledgeLibraryPathError(KnowledgeLibraryError):
    """路径越界或非法（越过资料库根目录一律拒绝 —— 约束 13）。"""


class KnowledgeLibraryNotFoundError(KnowledgeLibraryError):
    """目标不存在（目录 / 文件 / 索引）。"""


class KnowledgeLibraryTypeError(KnowledgeLibraryError):
    """不可作为文本读取的类型（二进制 / 目录）。"""


def resolve_root(explicit: str | None = None) -> Path:
    """解析资料库根目录（多级回退）。

    优先级：显式入参 > 配置项 `knowledge_library_dir` > 环境变量
    `EMILY_KNOWLEDGE_LIBRARY_DIR` > 容器挂载点 /app/knowledge_library >
    宿主机 `Emily-var/knowledge_library`（与代码仓库同级）> 旧仓库内路径（兼容）。

    资料库是运行态业务数据，**不在代码仓库内**：宿主机上位于 `Emily/` 同级的
    `Emily-var/`，容器内仍由 compose 挂载到 `/app/knowledge_library`。
    """
    if explicit and str(explicit).strip():
        return Path(str(explicit).strip()).expanduser()

    # ① 配置项（bootstrap 已把环境变量映射进 Config）
    try:
        from ...config import Config

        cfg_dir = (Config().knowledge_library_dir or "").strip()
        if cfg_dir:
            return Path(cfg_dir).expanduser()
    except Exception as e:  # noqa: BLE001 — 配置不可用时继续走后续回退
        logger.debug("knowledge_library config read failed: %s", e)

    # ② 环境变量（脚本 subprocess 通道，未必经过 bootstrap）
    env_dir = (os.environ.get("EMILY_KNOWLEDGE_LIBRARY_DIR") or "").strip()
    if env_dir:
        return Path(env_dir).expanduser()

    # ③ 容器挂载点（compose: ${VAR_DIR}/knowledge_library:/app/knowledge_library）
    container = Path("/app/knowledge_library")
    if container.exists():
        return container

    # ④ 宿主机开发：仓库同级 Emily-var/knowledge_library
    #    library_fs.py → knowledge_library/ → services/ → emily_core/ → emily-core/ → Emily/
    repo_root = Path(__file__).resolve().parents[4]
    sibling = repo_root.parent / "Emily-var" / "knowledge_library"
    if sibling.exists():
        return sibling

    # ⑤ 兼容迁移前的旧位置（存在才用，避免新克隆的人看不到资料库）
    legacy = repo_root / "emily-data" / "knowledge_library"
    if legacy.exists():
        logger.warning("knowledge_library 仍在旧位置 %s，建议迁移到 %s", legacy, sibling)
        return legacy

    return sibling


def _iso(ts: float) -> str:
    """文件时间戳 → ISO8601 字符串（本地时区，秒精度）。"""
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


class KnowledgeLibraryFs:
    """资料库文件仓储（sync）。"""

    def __init__(self, root: str | None = None):
        self._root = resolve_root(root)

    # ── 根目录 ──

    @property
    def root(self) -> Path:
        """解析后的资料库根（绝对路径）。"""
        return self._root

    # ── 路径解析 + 越界防护 ──

    def resolve(self, rel: str = "") -> Path:
        """仓库相对路径 → 绝对路径；越界抛 KnowledgeLibraryPathError。

        Args:
            rel: 仓库相对路径（"" 表示根目录本身）。

        Returns:
            绝对路径（不保证存在）。
        """
        root = self._root
        raw = (rel or "").strip().replace("\\", "/")
        if raw in ("", ".", "/"):
            return root

        candidate = Path(raw) if Path(raw).is_absolute() else root / raw
        try:
            resolved = candidate.resolve()
            root_resolved = root.resolve()
        except OSError as e:
            raise KnowledgeLibraryPathError(f"路径无法解析：{rel}") from e

        if resolved != root_resolved and not resolved.is_relative_to(root_resolved):
            raise KnowledgeLibraryPathError(f"路径越出资料库范围：{rel}")
        return resolved

    def _require_dir(self, rel: str) -> Path:
        p = self.resolve(rel)
        if not p.exists() or not p.is_dir():
            raise KnowledgeLibraryNotFoundError(f"资料库目录不存在：{rel or '/'}")
        return p

    # ── 列目录 ──

    def list_entries(self, rel: str = "") -> list[dict]:
        """列出目录下的直接子项（排除索引文件自身）。

        Args:
            rel: 目录相对路径（"" 表示根目录）。

        Returns:
            [{name, kind: "dir"|"file", size, mtime}]，目录在前、各自按名称排序。
        """
        d = self._require_dir(rel)
        entries: list[dict] = []
        try:
            children = sorted(d.iterdir(), key=lambda p: p.name)
        except OSError as e:
            raise KnowledgeLibraryError(f"目录读取失败：{rel}") from e

        for child in children:
            if child.name == INDEX_FILENAME:
                continue
            try:
                st = child.stat()
            except OSError:
                continue
            if child.is_dir():
                entries.append({"name": child.name, "kind": "dir", "size": 0, "mtime": _iso(st.st_mtime)})
            elif child.is_file():
                entries.append({"name": child.name, "kind": "file", "size": st.st_size, "mtime": _iso(st.st_mtime)})

        entries.sort(key=lambda e: (0 if e["kind"] == "dir" else 1, e["name"]))
        return entries

    # ── 读索引 ──

    def read_index(self, rel: str = "") -> str | None:
        """读取该目录的索引文件内容；不存在返回 None。

        rel 指向文件时按"其所在目录"解析（查阅侧读文件前需先校验同目录索引）。
        """
        p = self.resolve(rel)
        index_path = (p if p.is_dir() or not p.exists() else p.parent) / INDEX_FILENAME
        if not index_path.exists() or not index_path.is_file():
            return None
        try:
            return index_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise KnowledgeLibraryError(f"索引读取失败：{rel}") from e

    def index_exists(self, rel: str = "") -> bool:
        """该目录是否已入库（有索引文件）。"""
        p = self.resolve(rel)
        target = p if p.is_dir() or not p.exists() else p.parent
        return (target / INDEX_FILENAME).is_file()

    # ── 读文件 ──

    def read_text_file(self, rel: str, max_bytes: int = DEFAULT_MAX_READ_BYTES) -> str:
        """读取可读文本文件；二进制 / 目录抛 KnowledgeLibraryTypeError。

        超限按 max_bytes 截断，并在末尾标注截断说明。
        """
        p = self.resolve(rel)
        if not p.exists():
            raise KnowledgeLibraryNotFoundError(f"资料文件不存在：{rel}")
        if p.is_dir():
            raise KnowledgeLibraryTypeError(f"{rel} 是目录，不能作为文件读取")

        ext = p.suffix.lower()
        # 按扩展名分派：非文本白名单的类型一律拒绝（不解析内容，防"文本内容伪装成二进制类型"被当文本读出）
        if ext not in TEXT_EXTENSIONS:
            if ext in BINARY_EXTENSIONS:
                raise KnowledgeLibraryTypeError(
                    f"不可解析的类型（{ext}）：资料库不解析此类文件内容"
                )
            if self._looks_binary(p):
                raise KnowledgeLibraryTypeError(
                    f"不可解析的类型（{ext or '无扩展名'}）：资料库仅支持按文本读取 "
                    f"{'/'.join(sorted(TEXT_EXTENSIONS))} 等文本类文件"
                )

        try:
            size = p.stat().st_size
            with open(p, "rb") as f:
                blob = f.read(max_bytes)
        except OSError as e:
            raise KnowledgeLibraryError(f"资料文件读取失败：{rel}") from e

        text = blob.decode("utf-8", errors="replace")
        if size > max_bytes:
            text += f"\n\n[内容超长已截断：仅返回前 {max_bytes} 字节，共 {size} 字节]"
        return text

    @staticmethod
    def _looks_binary(p: Path) -> bool:
        """无扩展名 / 未知扩展名文件的内容嗅探（含 NUL 字节即判二进制）。"""
        try:
            with open(p, "rb") as f:
                head = f.read(_SNIFF_BYTES)
        except OSError:
            return True
        return b"\x00" in head

    # ── 写索引 ──

    def write_index(self, rel: str, content: str) -> Path:
        """覆盖写该目录的索引文件（幂等；不改动任何原始资料文件）。

        Args:
            rel: 目录相对路径。
            content: 索引全文。

        Returns:
            写入的索引文件绝对路径。
        """
        d = self._require_dir(rel)
        index_path = d / INDEX_FILENAME
        try:
            index_path.write_text(content, encoding="utf-8", newline="\n")
        except OSError as e:
            raise KnowledgeLibraryError(f"索引写入失败：{rel}") from e
        return index_path

    # ── 目录元信息（时效判定，US-09）──

    def dir_meta(self, rel: str = "") -> dict:
        """目录索引时效元信息。

        Returns:
            {index_mtime, newest_content_mtime, file_count}
            - index_mtime: 索引文件修改时间（ISO8601）或 None（未入库）
            - newest_content_mtime: 子树内**除索引文件外**最新修改时间（ISO8601）或 None
            - file_count: 子树内文件数（不含索引文件）

        时效判定：newest_content_mtime > index_mtime ⇒ 索引可能过期（由消费方提示，
        不阻断查阅 —— US-09.3）。
        """
        d = self._require_dir(rel)

        index_path = d / INDEX_FILENAME
        index_mtime: str | None = None
        if index_path.is_file():
            try:
                index_mtime = _iso(index_path.stat().st_mtime)
            except OSError:
                index_mtime = None

        newest: float | None = None
        file_count = 0
        for cur_dir, _dirnames, filenames in os.walk(d):
            for name in filenames:
                if name == INDEX_FILENAME:
                    continue
                try:
                    ts = os.stat(os.path.join(cur_dir, name)).st_mtime
                except OSError:
                    continue
                file_count += 1
                if newest is None or ts > newest:
                    newest = ts

        return {
            "index_mtime": index_mtime,
            "newest_content_mtime": _iso(newest) if newest is not None else None,
            "file_count": file_count,
        }
