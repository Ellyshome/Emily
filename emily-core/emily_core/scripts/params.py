"""params — 脚本参数 schema 的解析、校验与 CLI 拼装。

职责：把 Web 表单提交的 {参数名: 值} 字典，按 ScriptEntry.params 声明的 schema
校验后拼成 subprocess 可用的 argv 列表。

安全边界（重要）：
  ScriptManager.run() 是 subprocess 直传 argv（非 shell=True），本身无注入风险；
  但若放任前端传任意 args，等于把"执行任意参数"开放给调用方。
  因此 Web 通道只走本模块：**未在 schema 中声明的参数一律拒绝**（白名单语义），
  enum/multi 的取值必须落在 choices 内，int 必须可转且在 min/max 内。
"""

from __future__ import annotations

from .script_entry import ScriptEntry, ScriptParam, ScriptSubcommand


class ParamError(ValueError):
    """参数校验失败。message 直接面向调用方展示。"""


def parse_params(raw: list) -> list[ScriptParam]:
    """从 YAML 原始 list 构造 ScriptParam 列表。

    容错：单条解析失败跳过并继续，避免一个坏条目导致整个 registry 加载失败。
    """
    result: list[ScriptParam] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        result.append(ScriptParam(
            name=str(name),
            type=item.get("type", "str"),
            label=item.get("label", ""),
            help=item.get("help", ""),
            required=bool(item.get("required", False)),
            default=item.get("default"),
            choices=item.get("choices", []) or [],
            positional=bool(item.get("positional", False)),
            group=item.get("group"),
            min=item.get("min"),
            max=item.get("max"),
            options_source=item.get("options_source"),
        ))
    return result


def parse_subcommands(raw: list) -> list[ScriptSubcommand]:
    """从 YAML 原始 list 构造 ScriptSubcommand 列表。

    容错同 parse_params：单条解析失败跳过，不影响整个 registry 加载。
    """
    result: list[ScriptSubcommand] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        result.append(ScriptSubcommand(
            name=str(name),
            label=item.get("label", ""),
            help=item.get("help", ""),
            params=parse_params(item.get("params", [])),
        ))
    return result


def param_to_dict(p: ScriptParam) -> dict:
    """序列化为前端可消费的 JSON（表单渲染依据）。"""
    return {
        "name": p.name,
        "type": p.type,
        "label": p.label or p.name,
        "help": p.help,
        "required": p.required,
        "default": p.default,
        "choices": list(p.choices),
        "positional": p.positional,
        "group": p.group,
        "min": p.min,
        "max": p.max,
        "options_source": p.options_source,
    }


def subcommand_to_dict(sub: ScriptSubcommand) -> dict:
    """子命令序列化为前端可消费的 JSON。"""
    return {
        "name": sub.name,
        "label": sub.label or sub.name,
        "help": sub.help,
        "params": [param_to_dict(p) for p in sub.params],
    }


def build_cli_args(entry: ScriptEntry, values: dict,
                   subcommand: str | None = None) -> list[str]:
    """按 schema 把表单值拼成 argv。

    Args:
        entry: 目标脚本条目。
        values: {参数名: 值}，来自 Web 表单。未声明的键直接报错。
        subcommand: 带子命令脚本选中的动作名（如 "query"），拼在 argv 首位。

    Returns:
        argv 列表，如 ["query", "--project-id", "xxx"]。位置参数排在选项前。

    Raises:
        ParamError: 未声明 schema / 未知子命令 / 未知参数 / 缺必填 / 类型错 /
                    取值不在 choices / 越界 / 互斥组冲突。
    """
    if entry.subcommands:
        if not subcommand:
            raise ParamError(f"脚本 '{entry.name}' 有多个动作，请先选择子命令")
        sub = next((s for s in entry.subcommands if s.name == subcommand), None)
        if sub is None:
            names = ", ".join(s.name for s in entry.subcommands)
            raise ParamError(f"未知子命令：{subcommand}（可选：{names}）")
        return [sub.name, *_build_from_params(sub.params, values)]

    if subcommand:
        raise ParamError(f"脚本 '{entry.name}' 不接受子命令参数")

    if not entry.params:
        raise ParamError(f"脚本 '{entry.name}' 未声明参数 schema，不支持表单调用")

    return _build_from_params(entry.params, values)


def _build_from_params(params: list, values: dict) -> list[str]:
    """按 params schema 把表单值拼成 argv 片段（不含子命令名）。

    子命令分支可以没有参数，此时返回空列表。
    """
    if not params:
        return []

    by_name = {p.name: p for p in params}

    # 白名单校验：拒绝任何未声明的参数
    unknown = [k for k in values if k not in by_name]
    if unknown:
        raise ParamError(f"未知参数：{', '.join(sorted(unknown))}")

    positional: list[str] = []
    options: list[str] = []
    groups_seen: dict[str, str] = {}

    for p in params:
        raw = values.get(p.name)

        # 未提供 → 校验必填后跳过（不下发 default，交由脚本 argparse 自己的默认值）
        if raw is None or raw == "" or raw is False or raw == []:
            # 互斥组成员的 required 表达的是"整组必选其一"，由组级检查兜底，
            # 不能在单个成员缺席时就报错。
            if p.required and not p.group:
                raise ParamError(f"缺少必填参数：{p.label or p.name}")
            continue

        # 互斥组：同组只允许一个
        if p.group:
            prev = groups_seen.get(p.group)
            if prev:
                raise ParamError(f"参数 '{prev}' 与 '{p.name}' 互斥，只能选其一")
            groups_seen[p.group] = p.name

        if p.type == "flag":
            options.append(p.flag)

        elif p.type == "int":
            try:
                n = int(raw)
            except (TypeError, ValueError):
                raise ParamError(f"参数 '{p.label or p.name}' 需为整数，收到：{raw!r}")
            if p.min is not None and n < p.min:
                raise ParamError(f"参数 '{p.label or p.name}' 不得小于 {p.min}")
            if p.max is not None and n > p.max:
                raise ParamError(f"参数 '{p.label or p.name}' 不得大于 {p.max}")
            options.extend([p.flag, str(n)])

        elif p.type == "enum":
            s = str(raw)
            if p.choices and s not in p.choices:
                raise ParamError(f"参数 '{p.label or p.name}' 取值非法：{s!r}")
            if p.positional:
                positional.append(s)
            else:
                options.extend([p.flag, s])

        elif p.type == "multi":
            items = raw if isinstance(raw, list) else [raw]
            for it in items:
                s = str(it)
                if p.choices and s not in p.choices:
                    raise ParamError(f"参数 '{p.label or p.name}' 取值非法：{s!r}")
                options.extend([p.flag, s])

        else:  # str
            s = str(raw)
            if p.positional:
                positional.append(s)
            else:
                options.extend([p.flag, s])

    # 必填互斥组：schema 里整组 required 时，至少要选一个
    required_groups = {p.group for p in params if p.group and p.required}
    for g in required_groups:
        if g not in groups_seen:
            names = [p.name for p in params if p.group == g]
            raise ParamError(f"必须从 [{', '.join(names)}] 中选择一项")

    return positional + options
