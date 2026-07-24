"""
LLM 流量追踪 addon — 将 emily-core ↔ DeepSeek 的通讯以 jsonl 落盘。

开关：环境变量 LLM_TRACE_ENABLED=1 时激活，注释/删掉即关闭。
输出：/app/logs/llm_trace.jsonl（与 emily-core 共享 emily-data/logs 目录）

记录字段（仅保留开发者观察业务数据所需）：
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
    def _output_path(self) -> str:
        return os.environ.get("LLM_TRACE_OUTPUT", "/app/logs/llm_trace.jsonl")

    @staticmethod
    def _parse_json(text: str) -> dict | None:
        """安全解析 JSON 字符串，失败返回 None。"""
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            return None

    def response(self, flow: http.HTTPFlow):
        if not self._enabled:
            return
        if "api.deepseek.com" not in flow.request.host:
            return

        req_body = flow.request.text
        resp_body = flow.response.text

        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "request_body": req_body,
            "response_body": resp_body,
        }

        # 轻量解析：提取开发者高频关注字段到顶层，便于 grep/jq 统计；
        # 解析失败（非标准 JSON）则跳过，body 原文已保留，不丢信息。
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
            with open(self._output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 落盘失败不阻断代理


addons = [LLMTraceLogger()]
