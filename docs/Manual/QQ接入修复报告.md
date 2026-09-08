# QQ 接入修复报告（2026-09-08）

## 结论

QQ 私聊通道已端到端打通：NapCat（QQ `2680188817` 登录，OneBot11 反向 WebSocket）→ AstrBot `aiocqhttp(QQ)` → `emily_agent` → emily-core，回复经 SSE 回到 QQ。已多轮验证正常。

## 链路

```
QQ 私聊
  → NapCat（OneBot11 ws 客户端 ws://astrbot:6199/ws，30s 断线重连）
  → AstrBot aiocqhttp 平台（反向 WS server 0.0.0.0:6199）
  → emily_agent.on_message → StandardMessage（platform=aiocqhttp, private, conv=sender_id）
  → POST http://emily-core:18080/api/v1/message/send
  → emily-core 会话/意图/工具处理 → outbound SSE "reply"
  → astrbot 按 conversation_id 定位 event → 发回 QQ
```

## 改动清单

| 文件 | 改动 |
|---|---|
| `docker-compose-napcat.yml` | napcat 增加 volume：`emily-data/napcat_config`→`/app/napcat/config`、`emily-data/napcat_qq`→`/app/.config/QQ`（重建容器不丢配置与登录态） |
| `emily-data/napcat_config/onebot11_2680188817.json` | `network.websocketClients` 增加 `ws://astrbot:6199/ws`（NapCat 官方 AstrBot 模板） |
| `data/cmd_config.json` | `platform` 增加 `aiocqhttp`：id=QQ, ws_reverse_host=0.0.0.0, ws_reverse_port=6199 |
| `data/plugins/emily_agent/adapters/astrbot/inbound_adapter.py` | ① 私聊枚举 `PRIVATE_MESSAGE`→`FRIEND_MESSAGE`；② 补 `mentioned_ids`/`is_at_bot` 初始化 |
| emily-core 数据库 | 预注册用户「禁锢之灵」`88371831` 绑定 `aiocqhttp`（管理员注册路径） |

## 排障发现的 4 个问题

1. **AstrBot 无 QQ 适配器、NapCat OneBot11 网络为空**：QQ 登录后消息只打印在 NapCat 控制台无人消费。补齐上述 NapCat ws 客户端 + AstrBot aiocqhttp 平台即通。
2. **inbound_adapter 私聊枚举不存在**：`MessageType.PRIVATE_MESSAGE` 在 AstrBot v4.27.5 实际叫 `FRIEND_MESSAGE`；且 `mentioned_ids`/`is_at_bot` 在 At 组件循环前未初始化，首次运行必崩（QQ 是第一个走通该通用路径的平台）。
3. **core 用户门禁拒绝新平台用户**：`auto_create_user` 默认 `False`（BUG-002 门禁，Config 无注入点），新 QQ 用户报「未授权的发送者/请联系管理员注册」→ 按设计走管理员注册：`user_im_bindings` 预插 `(aiocqhttp, 88371831)` 行即可放行。
4. **插件内容去重吞消息**：`emily_agent` 以 `session_id+内容` SHA256 去重（deque 200），同会话连续发相同文本会被当重复丢弃、无任何日志。联调时换文案即可绕开。

## 验证记录

- 08:58 私聊「你是谁？」→ 回复「我是艾米（Emily）…」
- 08:58 私聊「我在什么节点里么？」→ 回复（未查到节点）
- 09:02 私聊「当前在运行着什么项目？」→ 命中 `SOP-005-QRY`，调用 `query_data` 查到项目「翠湖庭院住宅小区 (EMERALD-01)」

## 遗留事项

- 容器**重建**后 QQ 会因设备指纹变化要求重扫码一次（登录数据已持久化到 host volume，扫码一次即恢复）。
- wecom(Emily) 平台仍是占位凭据（secret=`111` → 40001），未在本次范围。
- 群聊规则：仅 @ 机器人且发送者已注册才接管；群内未注册用户 @ 会触发与问题 3 相同的 500（需为其预注册）。
- `wxmp`（小程序网关）与 QQ 同受 `auto_create_user=False` 门禁，新用户需预注册（Phase 2 接真实身份时处理）。
