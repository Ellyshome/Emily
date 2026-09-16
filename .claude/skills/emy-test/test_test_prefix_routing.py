"""测试前缀 + 交互通道 —— live 对照用例。

验证控制台左侧栏「测试前缀 / 交互通道」的端到端效果：
  S1 前缀为空（保存 []）→ 任何会话都不算测试会话：事件 test_session=false，
     即「前缀空 = 不拦截，按真实投递处理」
  S2 前缀 = test_ → conv=test_xxx 事件 test_session=true（只走 SSE，不外发真实 IM）；
     未命中前缀的会话仍为 false（真实通道不受影响）
  S3 交互通道 = napcat → emy-test CLI/探针取到的默认 platform 变为 napcat
  S4 复位默认（前缀 test_,conv_；交互通道 simulator）

用法：
    python .claude/skills/emy-test/test_test_prefix_routing.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_SKILL_DIR = Path(__file__).resolve().parent
if str(_SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(_SKILL_DIR))

import aiohttp

from config_loader import get_api_token, get_core_url, get_interaction_channel
from tester import EmysTester
from test_identity_and_outbound import _RawSSECapture, _terminate_session

REAL_QQ = "123456010"
API = "/api/v1/console/test-settings"


def _headers() -> dict:
    token = get_api_token()
    return {"X-Emily-Token": token} if token else {}


async def _post_state(payload: dict) -> dict:
    async with aiohttp.ClientSession(headers=_headers()) as s:
        async with s.post(f"{get_core_url()}{API}", json=payload) as resp:
            body = await resp.json()
            print(f"   [settings] POST {payload} → {body.get('message')}")
            return (body.get("data") or {}).get("state") or {}


async def _send_and_capture(cap: _RawSSECapture, cid: str, platform: str) -> list:
    await _terminate_session(cid)   # 清会话缓存，确保重新拉起
    with EmysTester() as emy:
        await emy.send_message(
            "你好", sender_id=REAL_QQ, sender_name="黄志强",
            platform=platform, conversation_type="private", conversation_id=cid,
        )
    await asyncio.sleep(1.0)
    return cap.reply_frames(cid)


async def _run() -> bool:
    print("=" * 78)
    print(f"测试前缀 / 交互通道 live 用例  |  core={get_core_url()}")
    print("=" * 78)
    results: dict[str, bool] = {}

    try:
        async with _RawSSECapture() as cap:
            # S1 前缀为空：不拦截
            print("\n── S1 测试前缀为空 → 不做拦截（按真实投递）")
            await _post_state({"test_prefixes": [], "interaction_channel": "napcat"})
            frames = await _send_and_capture(cap, "test_prefix_off_01", "napcat")
            flags = [f["data"].get("test_session") for f in frames]
            print(f"   SSE test_session={flags}（期望 [False]）")
            results["S1_empty_prefix_not_intercepted"] = flags == [False]

            # S2 前缀 test_：命中即拦截，未命中不受影响
            print("\n── S2 测试前缀=test_ → 命中拦截、未命中不受影响")
            await _post_state({"test_prefixes": ["test_"], "interaction_channel": "napcat"})
            hit = [f["data"].get("test_session") for f in
                   await _send_and_capture(cap, "test_prefix_on_01", "napcat")]
            miss = [f["data"].get("test_session") for f in
                    await _send_and_capture(cap, str(REAL_QQ), "napcat")]
            print(f"   命中 conv=test_prefix_on_01 → {hit}（期望 [True]）")
            print(f"   未命中 conv={REAL_QQ} → {miss}（期望 [False]）")
            results["S2_prefix_hit_intercepted"] = hit == [True]
            results["S2_prefix_miss_not_intercepted"] = miss == [False]

            # S3 交互通道联动
            print("\n── S3 交互通道=napcat → 工具默认 platform 跟随")
            state = await _post_state({"test_prefixes": ["test_"], "interaction_channel": "napcat"})
            resolved = get_interaction_channel()
            print(f"   state.interaction_channel={state.get('interaction_channel')} "
                  f"tools_default={resolved}")
            results["S3_interaction_channel_followed"] = resolved == "napcat"
    finally:
        # S4 复位默认
        print("\n── S4 复位默认（前缀 test_,conv_；交互通道 simulator）")
        state = await _post_state({"test_prefixes": ["test_", "conv_"],
                                   "interaction_channel": "simulator"})
        results["S4_reset"] = (state.get("test_prefixes") == ["test_", "conv_"]
                              and state.get("interaction_channel") == "simulator")

    print("\n" + "=" * 78)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    passed = sum(1 for v in results.values() if v)
    print(f"通过 {passed}/{len(results)}")
    print("=" * 78)
    return passed == len(results)


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(_run()) else 1)
