"""
LLM 流量追踪 addon — 将 emily-core ↔ DeepSeek 的通讯以 jsonl + md 落盘。

开关：环境变量 LLM_TRACE_ENABLED=1 时激活，注释/删掉即关闭。
输出：
  - /app/logs/llm_trace.jsonl  — 机器可读（AI 工具、grep/jq），与 emily-core 共享
  - /app/logs/llm_trace.md     — 人类可读，将 jsonl 全量数据原样转换为 markdown 结构，不丢失任何字段

jsonl 记录字段（仅保留开发者观察业务数据所需）：
  - timestamp：时间戳
  - request_body：请求原文（messages 全文、model、采样参数）
  - response_body：响应原文（LLM 输出 content、finish_reason、usage）
  - model：请求模型（从 request_body 解析，便于 grep 统计）
  - messages_count：消息条数（从 request_body 解析）
  - usage：token 消耗（从 response_body 解析，prompt/completion/total/cache）
  - finish_reason：完成原因（stop/length 等）

已过滤的无价值 SDK 字段：url / method / status_code / request_headers /
response_headers（Host、User-Agent、X-Stainless-* 等均为传输层元信息，
且 request_headers 含明文 API key，记录存在泄露风险）。
body 解析失败时仅保留 request_body / response_body 原文，不阻断记录。
"""
import json
import os
import time
from mitmproxy import http


class LLMTraceLogger:

    def __init__(self):
        self._enabled = os.environ.get("LLM_TRACE_ENABLED", "") == "1"

    @property
    def _output_base(self) -> str:
        """基础路径前缀，jsonl 和 md 共用。"""
        return os.environ.get("LLM_TRACE_OUTPUT", "/app/logs/llm_trace")

    @property
    def _output_jsonl(self) -> str:
        return self._output_base + ".jsonl"

    @property
    def _output_md(self) -> str:
        return self._output_base + ".md"

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """安全解析 JSON 字符串，失败返回 None。"""
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _json_to_md(value, depth: int = 3) -> str:
        """将任意 JSON 值递归渲染为 markdown，不丢失任何字段。

        - dict: 使用 `#` * depth 标题作为 key，内容继续递归
        - list of dict: 逐项编号渲染
        - list of str/int/...: 直接列出
        - str: 直接输出原文（长文本不截断）
        - 其他标量: 直接输出
        """
        if value is None:
            return ""
        if isinstance(value, bool):
            return str(value)
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value

        if isinstance(value, list):
            if not value:
                return ""
            # 如果列表元素全是 dict，逐项渲染，项之间用 `---` 分隔
            if all(isinstance(item, dict) for item in value):
                parts = []
                for i, item in enumerate(value):
                    item_content = LLMTraceLogger._json_to_md(item, depth)
                    if item_content:
                        parts.append(item_content)
                return "\n---\n".join(parts)
            # 其他列表直接逐行输出
            return "\n".join(str(item) for item in value)

        if isinstance(value, dict):
            prefix = "#" * depth
            parts = []
            idx = 0
            keys = list(value.keys())
            for key, val in value.items():
                parts.append(f"{prefix} {key}")
                rendered = LLMTraceLogger._json_to_md(val, depth + 1)
                if rendered:
                    parts.append(rendered)
                # 同一层级的 dict 之间加空行分隔
                if idx < len(keys) - 1:
                    parts.append("")
                idx += 1
            return "\n".join(parts)

        return str(value)

    def response(self, flow: http.HTTPFlow):
        if not self._enabled:
            return
        if "api.deepseek.com" not in flow.request.host:
            return

        req_body = flow.request.text
        resp_body = flow.response.text

        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())

        # ── jsonl 记录 ──
        record = {
            "timestamp": timestamp,
            "request_body": req_body,
            "response_body": resp_body,
        }

        req_json = self._parse_json(req_body)
        if req_json:
            record["model"] = req_json.get("model", "")
            messages = req_json.get("messages")
            if isinstance(messages, list):
                record["messages_count"] = len(messages)

        resp_json = self._parse_json(resp_body)
        if resp_json:
            usage = resp_json.get("usage")
            if usage:
                record["usage"] = usage
            choices = resp_json.get("choices")
            if isinstance(choices, list) and choices:
                record["finish_reason"] = choices[0].get("finish_reason", "")

        try:
            with open(self._output_jsonl, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass

        # ── md 记录（jsonl 全量数据 → 结构化 markdown，不丢失任何字段）──
        try:
            self._append_md(timestamp, req_body, resp_body, req_json, resp_json)
        except Exception:
            pass

    def _append_md(self, timestamp: str, req_body: str, resp_body: str,
                   req_json: dict | None, resp_json: dict | None):
        """将 jsonl 全量数据渲染为 markdown。"""
        entry = f"## {timestamp}\n\n"

        # ── Request ──
        entry += "### Request Body\n\n"
        if req_json:
            # 先将 messages 单独提取渲染（它们是最核心的长文内容）
            messages = req_json.get("messages")
            req_meta = {k: v for k, v in req_json.items() if k != "messages"}
            if req_meta:
                entry += "```json\n" + json.dumps(req_meta, ensure_ascii=False, indent=2) + "\n```\n\n"
            if isinstance(messages, list):
                entry += "#### messages\n\n"
                entry += self._json_to_md(messages, depth=3)
        else:
            entry += "```\n" + req_body + "\n```\n"

        # ── Response ──
        entry += "\n### Response Body\n\n"
        if resp_json:
            # 先渲染元信息（id/object/created/model/system_fingerprint 等）
            resp_meta = {k: v for k, v in resp_json.items() if k not in ("choices", "usage")}
            if resp_meta:
                entry += "```json\n" + json.dumps(resp_meta, ensure_ascii=False, indent=2) + "\n```\n\n"
            # usage
            usage = resp_json.get("usage")
            if usage:
                entry += "#### usage\n\n"
                entry += "```json\n" + json.dumps(usage, ensure_ascii=False, indent=2) + "\n```\n\n"
            # choices（含 message.content + reasoning_content）
            choices = resp_json.get("choices")
            if isinstance(choices, list):
                entry += "#### choices\n\n"
                entry += self._json_to_md(choices, depth=3)
        else:
            entry += "```\n" + resp_body + "\n```\n"

        entry += "\n---\n"
        with open(self._output_md, "a", encoding="utf-8") as f:
            f.write(entry)


addons = [LLMTraceLogger()]
