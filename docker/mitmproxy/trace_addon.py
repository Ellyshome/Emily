"""
LLM 流量追踪 addon — 将 emily-core ↔ DeepSeek 的通讯以 jsonl 落盘。

开关：环境变量 LLM_TRACE_ENABLED=1 时激活，注释/删掉即关闭。
输出：/app/logs/llm_trace.jsonl（与 emily-core 共享 emily-data/logs 目录）
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

    def response(self, flow: http.HTTPFlow):
        if not self._enabled:
            return
        if "api.deepseek.com" not in flow.request.host:
            return

        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
            "url": flow.request.pretty_url,
            "method": flow.request.method,
            "status_code": flow.response.status_code,
            "request_headers": dict(flow.request.headers),
            "request_body": flow.request.text,
            "response_headers": dict(flow.response.headers),
            "response_body": flow.response.text,
        }
        try:
            with open(self._output_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            pass  # 落盘失败不阻断代理


addons = [LLMTraceLogger()]
