"""EmyTester Web 控制台 —— 直连 emily-core 容器（生产环境实战测试）。

启动: python .claude/skills/emy-test/emy_web/app.py
默认监听 http://localhost:8000

本 Web UI 直接使用 EmilyApiClient 与 emily-core 通信，模拟 astrbot 插件行为。
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

# 确保能导入 skill 兄弟模块
_skill_dir = Path(__file__).resolve().parent.parent
if str(_skill_dir) not in sys.path:
    sys.path.insert(0, str(_skill_dir))

import gradio as gr

from config_loader import get_core_url, get_llm_config


# ── 文件上传 / 对话导出辅助函数 ──

def _infer_attachment_type(filepath: str) -> int:
    """根据文件扩展名推断附件类型。"""
    ext = Path(filepath).suffix.lower()
    if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
        return 2  # image
    elif ext in (".mp3", ".wav", ".ogg", ".aac", ".m4a"):
        return 4  # voice
    elif ext in (".mp4", ".avi", ".mov", ".mkv", ".webm"):
        return 5  # video
    return 3  # file


def _save_uploaded_file(file_obj, target_dir: Path) -> dict | None:
    """保存 Gradio 上传的文件到模拟器目录。

    Returns:
        {"type": 3, "url": "file:///...", "file_name": "...", "file_size": ...} or None
    """
    try:
        if isinstance(file_obj, str):
            src = Path(file_obj)
            if not src.exists():
                return None
            content = src.read_bytes()
            filename = src.name
        elif isinstance(file_obj, dict):
            # Gradio >= 4.x file format
            content = Path(file_obj.get("path", "")).read_bytes()
            filename = Path(file_obj.get("name", "unnamed")).name
        else:
            return None

        if not content:
            return None

        date_dir = datetime.now().strftime("%Y-%m")
        save_dir = target_dir / date_dir
        save_dir.mkdir(parents=True, exist_ok=True)

        stem = Path(filename).stem
        suffix = Path(filename).suffix or ""
        unique_name = f"{uuid.uuid4().hex[:8]}_{stem}{suffix}"
        file_path = save_dir / unique_name
        file_path.write_bytes(content)

        atype = _infer_attachment_type(filename)

        return {
            "type": atype,
            "url": file_path.as_uri(),
            "file_name": filename,
            "file_size": len(content),
        }
    except Exception as e:
        print(f"[Web] 保存上传文件失败: {e}")
        return None


def _format_web_conversation(chat_history: list[dict], fmt: str) -> str:
    """将 Gradio chat_history 格式化为可保存的文本。"""
    if fmt == "json":
        return json.dumps({
            "exported_at": datetime.now().isoformat(),
            "message_count": len(chat_history),
            "messages": [
                {"role": m.get("role", ""), "content": m.get("content", "")}
                for m in chat_history
            ],
        }, ensure_ascii=False, indent=2)

    if fmt == "markdown":
        lines = [
            "# Emily Core Web 对话记录",
            "",
            f"- 导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 消息数: {len(chat_history)}",
            "",
            "---",
            "",
        ]
        for m in chat_history:
            role = "🧑 用户" if m.get("role") == "user" else "🤖 Emily"
            content = m.get("content", "")
            lines.append(f"### {role}")
            lines.append("")
            lines.append(content)
            lines.append("")
            lines.append("---")
            lines.append("")
        return "\n".join(lines)

    # txt
    lines = [
        f"Emily Core Web 对话记录",
        f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"消息数: {len(chat_history)}",
        "=" * 50,
        "",
    ]
    for m in chat_history:
        role = "[用户]" if m.get("role") == "user" else "[Emily]"
        content = m.get("content", "")
        lines.append(f"{role} {content}")
        lines.append("-" * 40)
        lines.append("")
    return "\n".join(lines)


# ── 模拟器文件存储目录 ──

_PROJECT_ROOT = _skill_dir.parent.parent.parent.parent
_SIMULATOR_FILES_DIR = _PROJECT_ROOT / "data" / "tem_files" / "simulator"


# ── 核心聊天逻辑 ──


def send_message_sync(
    message: str,
    chat_history: list,
    uploaded_files: list,
    sender_name: str,
    sender_id: str,
    platform: str,
    conversation_type: str,
    group_id: str,
    is_at_bot: bool,
    request: gr.Request,
):
    """Gradio 提交处理 —— 直连 emily-core（模拟 astrbot 插件）。"""
    if not message or not message.strip():
        return "", chat_history, uploaded_files

    from tester import EmysTester

    # 构建用户消息的显示内容
    user_display = message
    attachments = None

    if uploaded_files:
        file_names = []
        attachments = []
        for f_obj in uploaded_files:
            saved = _save_uploaded_file(f_obj, _SIMULATOR_FILES_DIR)
            if saved:
                attachments.append(saved)
                file_names.append(saved["file_name"])
        if file_names:
            user_display += "\n\n📎 附件: " + ", ".join(file_names)

    try:
        # 使用唯一的 sender_id 确保 Web 会话隔离
        sid = sender_id.strip() if sender_id.strip() else f"web_{request.session_hash[:8]}"
        sname = sender_name.strip() if sender_name.strip() else sid
        cid = sid  # 私聊用 sender_id 作为 conversation_id

        if conversation_type == "group":
            cid = group_id.strip() if group_id.strip() else f"web_group_{request.session_hash[:8]}"

        with EmysTester() as emy:
            reply = emy.send_sync(
                message,
                sender_id=sid,
                sender_name=sname,
                platform=platform,
                conversation_type=conversation_type,
                conversation_id=cid,
                group_id=group_id.strip() if (conversation_type == "group" and group_id.strip()) else None,
                is_at_bot=is_at_bot if conversation_type == "group" else False,
                attachments=attachments,
            )

        if reply:
            reply_text = reply.content or "_(空回复)_"
        else:
            reply_text = "_(未接管，无回复)_"

        # 追加 Bot 发出的文件链接
        sent_files = emy.sent_files if reply else []
        if sent_files:
            file_links = []
            for sf in sent_files:
                name = sf.get("name", "file")
                caption = sf.get("caption", "")
                label = f"{name}" + (f" — {caption}" if caption else "")
                file_links.append(f"📎 {label} ({sf['path']})")
            reply_text += "\n\n" + "\n".join(file_links)

    except RuntimeError as e:
        reply_text = f"❌ {e}"
    except Exception as e:
        reply_text = f"❌ 错误: {e}"

    chat_history.append({"role": "user", "content": user_display})
    chat_history.append({"role": "assistant", "content": reply_text})
    return "", chat_history, None  # clear file input


def export_conversation(
    chat_history: list,
    save_dir: str,
    export_format: str,
) -> str:
    """将当前 Web 对话记录导出到指定目录。"""
    if not chat_history:
        return "⚠️ 没有对话记录可导出"

    if not save_dir or not save_dir.strip():
        return "⚠️ 请填写保存目录路径"

    save_path = Path(save_dir.strip())
    if not save_path.exists():
        try:
            save_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return f"❌ 无法创建目录: {e}"

    if not save_path.is_dir():
        return f"❌ 路径不是目录: {save_path}"

    content = _format_web_conversation(chat_history, export_format)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = {"markdown": "md", "json": "json", "txt": "txt"}[export_format]
    filename = f"emily_conversation_{timestamp}.{ext}"
    filepath = save_path / filename

    try:
        filepath.write_text(content, encoding="utf-8")
        rounds = len(chat_history) // 2
        return f"✅ 已保存: {filepath} ({rounds} 轮对话)"
    except Exception as e:
        return f"❌ 写入失败: {e}"


# ── Gradio UI ──

CUSTOM_CSS = """
footer { display: none !important; }
"""


def build_ui():
    llm_available = bool(get_llm_config())
    llm_status = "🟢 LLM 已配置" if llm_available else "⚪ LLM 未配置"
    default_download_dir = str(Path.home() / "Downloads")
    core_url = get_core_url()

    with gr.Blocks(title="Emily Core 测试控制台") as demo:
        gr.Markdown(
            f"# Emily Core 测试控制台   `{llm_status}`\n\n"
            f"emily-core: `{core_url}` | 模拟 astrbot 插件通信"
        )

        with gr.Row():
            with gr.Column(scale=1, min_width=260):
                gr.Markdown("### ⚙️ 消息参数")

                sender_name = gr.Textbox(
                    label="发送者名称", value="张工",
                    placeholder="QQ/微信昵称",
                )
                sender_id = gr.Textbox(
                    label="发送者 ID", value="",
                    placeholder="留空自动生成",
                )
                platform = gr.Dropdown(
                    label="平台",
                    choices=["simulator", "napcat", "wechat", "dingtalk", "feishu"],
                    value="simulator",
                )
                conversation_type = gr.Radio(
                    label="会话类型",
                    choices=["private", "group"],
                    value="private",
                )
                group_id = gr.Textbox(
                    label="群号（群聊时生效）", value="group_001",
                )
                is_at_bot = gr.Checkbox(
                    label="@了机器人", value=True,
                )

                gr.Markdown("### 📎 文件附件")
                uploaded_files = gr.File(
                    label="选择文件（模拟发送）",
                    file_count="multiple",
                    file_types=None,
                )

                gr.Markdown("### 📥 对话记录")
                save_dir = gr.Textbox(
                    label="保存目录",
                    value=default_download_dir,
                    placeholder="D:\\exports",
                )
                export_format = gr.Dropdown(
                    label="导出格式",
                    choices=["markdown", "json", "txt"],
                    value="markdown",
                )
                export_btn = gr.Button("📥 下载对话记录", size="sm")
                export_status = gr.Textbox(
                    label="", interactive=False, container=False,
                )

            with gr.Column(scale=3):
                chat = gr.Chatbot(
                    label="对话", height=550,
                    avatar_images=(None, None),
                )
                msg_input = gr.Textbox(
                    label="输入消息",
                    placeholder="输入测试消息，按 Enter 发送...",
                    scale=4,
                )

        msg_input.submit(
            fn=send_message_sync,
            inputs=[
                msg_input, chat, uploaded_files,
                sender_name, sender_id, platform,
                conversation_type, group_id, is_at_bot,
            ],
            outputs=[msg_input, chat, uploaded_files],
        )

        export_btn.click(
            fn=export_conversation,
            inputs=[chat, save_dir, export_format],
            outputs=[export_status],
        )

    return demo


if __name__ == "__main__":
    demo = build_ui()
    demo.queue(default_concurrency_limit=5)
    demo.launch(
        server_name="0.0.0.0",
        server_port=8000,
        share=False,
        inbrowser=True,
        css=CUSTOM_CSS,
    )
