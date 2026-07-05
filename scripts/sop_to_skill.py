#!/usr/bin/env python3
"""SOP-to-Skill 转换器 —— 用 LLM 将 SOP .md 转换为 Skill YAML。

用法：
    uv run python scripts/sop_to_skill.py --sop SOP-002-REC --dry-run
    uv run python scripts/sop_to_skill.py --sop SOP-002-REC
    uv run python scripts/sop_to_skill.py --all
"""

import argparse
import os
import sys
from pathlib import Path

# 添加项目根目录到 sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "emily-core"))


def _find_sop_dir() -> Path:
    """多级 fallback 查找 SOP 目录。"""
    candidates = [
        PROJECT_ROOT / "emily-data" / "sops",
        Path("/app/sops"),
    ]
    for d in candidates:
        if d.exists():
            return d
    raise FileNotFoundError("SOP 目录未找到")


def _find_skill_dir() -> Path:
    """查找或创建 Skill 目录。"""
    candidates = [
        PROJECT_ROOT / "emily-data" / "skills",
        Path("/app/skills"),
    ]
    for d in candidates:
        if d.exists():
            return d
    # 创建默认目录
    default = candidates[0]
    default.mkdir(parents=True, exist_ok=True)
    return default


def _load_sop(sop_id: str, sop_dir: Path) -> str:
    """加载 SOP .md 文件内容。"""
    # 按编号模糊匹配
    for f in sop_dir.glob(f"*{sop_id}*.md"):
        return f.read_text(encoding="utf-8")
    raise FileNotFoundError(f"SOP 文件未找到: {sop_id} (在 {sop_dir})")


def _call_llm(sop_text: str, api_key: str, base_url: str, model: str) -> str:
    """调用 LLM 将 SOP 转换为 Skill YAML。"""
    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    prompt_path = PROJECT_ROOT / "emily-data" / "prompts" / "sop_to_skill.md"
    if prompt_path.exists():
        system_prompt = prompt_path.read_text(encoding="utf-8")
    else:
        system_prompt = "将以下 SOP 文档转换为 Skill YAML（三段结构：instructions / tools / steps），不要输出 datasets 段。"

    system_prompt = system_prompt.replace("{sop_text}", sop_text)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "请转换此 SOP 文档为 Skill YAML。"},
        ],
        temperature=0,
    )

    content = response.choices[0].message.content or ""

    # 提取 YAML 代码块
    if "```yaml" in content:
        content = content.split("```yaml")[1].split("```")[0]
    elif "```" in content:
        content = content.split("```")[1].split("```")[0]

    return content.strip()


def _validate_yaml(text: str) -> bool:
    """验证 YAML 格式。"""
    try:
        import yaml
        data = yaml.safe_load(text)
        return isinstance(data, dict) and "skill_id" in data and "steps" in data
    except Exception:
        return False


def convert_sop(sop_id: str, dry_run: bool = False) -> str | None:
    """转换单个 SOP。"""
    sop_dir = _find_sop_dir()
    skill_dir = _find_skill_dir()

    sop_text = _load_sop(sop_id, sop_dir)
    print(f"加载 SOP: {sop_id} ({len(sop_text)} chars)")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

    if not api_key:
        print("错误: 未设置 DEEPSEEK_API_KEY 环境变量")
        return None

    yaml_text = _call_llm(sop_text, api_key, base_url, model)

    if not _validate_yaml(yaml_text):
        print("警告: LLM 输出不是合法 Skill YAML，请人工审校")

    if dry_run:
        print("\n" + "=" * 60)
        print(yaml_text)
        print("=" * 60)
        return yaml_text

    # 写入文件
    # 从 YAML 解析 skill_id 确定文件名
    import yaml
    data = yaml.safe_load(yaml_text)
    skill_id = data.get("skill_id", sop_id)
    output_path = skill_dir / f"{skill_id}.skill.yaml"
    output_path.write_text(yaml_text, encoding="utf-8")
    print(f"写入: {output_path}")
    return yaml_text


def _notify_reload() -> bool:
    """转换完成后通知 EmilyCore 热重载 Skill 注册表。"""
    import json
    try:
        import urllib.request
        core_host = os.environ.get("EMILY_CORE_HOST", "localhost")
        core_port = os.environ.get("EMILY_CORE_PORT", "18080")
        url = f"http://{core_host}:{core_port}/api/v1/skills/reload"
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=5) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if result.get("ok"):
                print(f"✓ EmilyCore 热重载成功: {result.get('total', 0)} skill(s)")
                return True
            else:
                print(f"✗ EmilyCore 热重载失败: {result.get('error', '未知错误')}")
                return False
    except Exception as e:
        print(f"⚠ 无法通知 EmilyCore 热重载（容器未运行？）: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SOP-to-Skill 转换器")
    parser.add_argument("--sop", help="SOP 编号（如 SOP-002-REC）")
    parser.add_argument("--all", action="store_true", help="批量转换全部 SOP")
    parser.add_argument("--dry-run", action="store_true", help="输出到 stdout 不写文件")
    parser.add_argument("--notify", action="store_true", help="转换完成后通知 EmilyCore 热重载")
    args = parser.parse_args()

    if not args.sop and not args.all:
        parser.print_help()
        return

    converted = 0
    if args.all:
        sop_dir = _find_sop_dir()
        for f in sorted(sop_dir.glob("SOP-*.md")):
            # 提取 SOP ID
            sop_id = f.stem.split("-")[0] + "-" + f.stem.split("-")[1] + "-" + f.stem.split("-")[2]
            print(f"\n转换: {sop_id}")
            result = convert_sop(sop_id, dry_run=args.dry_run)
            if result:
                converted += 1
    else:
        result = convert_sop(args.sop, dry_run=args.dry_run)
        if result:
            converted += 1

    # 热重载通知
    if args.notify and converted > 0 and not args.dry_run:
        print(f"\n已转换 {converted} 个 Skill，通知 EmilyCore 热重载...")
        _notify_reload()


if __name__ == "__main__":
    main()
