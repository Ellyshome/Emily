# AstrBot 微信客服接入配置指南

## 前提条件

- AstrBot 版本 >= v3.5.7（当前项目使用 `soulter/astrbot:latest`，满足）
- 拥有企业微信账号
- 服务器有公网 IP，且回调端口已放行

---

## 一、需要配置的参数

| 参数 | 说明 | 从哪里获取 |
|------|------|-----------|
| **corpid** | 企业 ID | 企业微信后台 → 我的企业 → 企业信息 |
| **secret** | 微信客服 Secret | 微信客服后台 → 开发配置 → 企业内部接入 |
| **token** | 回调验证 Token（3-32 位） | 微信客服回调配置页 → 随机获取 |
| **encoding_aes_key** | 消息加密密钥（43 位） | 微信客服回调配置页 → 随机获取 |
| **kf_name** | 客服账号**名称**（非账号 ID） | 微信客服后台 → 客服账号 → 创建后记下名称 |
| **port** | 回调监听端口，默认 `6195` | 自定，需公网可达 |
| **callback_server_host** | 监听地址 | 默认 `0.0.0.0` |
| **unified_webhook_mode** | 统一 Webhook 模式（v4.8.0+） | 建议设为 `true` |

---

## 二、需要改动的文件

### 1. `data/cmd_config.json` — AstrBot 核心配置

当前状态：`"platform": []` — 空数组，尚未配置任何平台适配器。

需要两个改动：

**(1) `"platform"` 数组添加 `"wecom"`**

**(2) `"platform_specific"` 下新增 `"wecom"` 配置块**

完整示例：

```json
{
  "platform": ["wecom"],
  "platform_specific": {
    "lark": { "...": "..." },
    "telegram": { "...": "..." },
    "discord": { "...": "..." },
    "wecom": {
      "corpid": "ww1234567890abcdef",
      "secret": "从微信客服开发配置获取",
      "token": "随机生成的3-32位token",
      "encoding_aes_key": "随机生成的43位AES密钥",
      "kf_name": "你的客服账号名称",
      "port": 6195,
      "callback_server_host": "0.0.0.0",
      "unified_webhook_mode": true
    }
  }
}
```

> **推荐方式**：通过 AstrBot Dashboard 配置。打开 `http://<服务器IP>:6185` → 消息平台 → 新增适配器 → 选择 wecom → 在 UI 中填写参数保存。AstrBot 会自动写入 `cmd_config.json`，避免手写 JSON 格式错误。

### 2. `docker-compose.yml` — 端口暴露

当前 astrbot 服务已暴露端口：

```yaml
ports:
  - "6185:6185"    # Dashboard
  - "6199:6199"    # 统一 Webhook 端口
```

**使用统一 Webhook 模式（推荐）**：6199 已暴露，**无需改动**。

**不使用统一 Webhook，需独立端口**：加一行暴露 6195：

```yaml
ports:
  - "6185:6185"    # Dashboard
  - "6199:6199"    # 统一 Webhook 端口
  - "6195:6195"    # 微信客服回调端口
```

> 无论哪种模式，服务器防火墙/安全组需放行对应端口。

### 3. `data/plugins/emily_agent/` — 薄插件（需新建）

当前 `/home/data/plugins/` 目录为空。需创建 `emily_agent` 插件，负责桥接 AstrBot 与 Emily 内核：

```
data/plugins/emily_agent/
├── main.py                         # AstrBot 插件入口（~100行，无业务逻辑）
├── adapters/
│   ├── astrbot/
│   │   ├── inbound_adapter.py      # AstrBot 消息 → StandardMessage → POST emily-core
│   │   └── outbound_sender.py      # SSE 监听 emily-core → AstrBot 发消息
│   └── standard/
│       └── message.py              # StandardMessage DTO 副本（不 import Core 包）
└── metadata.yaml                   # 插件元信息
```

接口协议（emily-core 端已完整实现）：

- **入站**：`POST /api/v1/message/send` — body 为 StandardMessage JSON
- **出站**：`GET /api/v1/events/outbound` — SSE 流，15s keep-alive 心跳
- **认证**：若 `EMILY_API_TOKEN` 环境变量已设置，需在请求头带 `X-Emily-Token`

---

## 三、接入步骤

| 步骤 | 操作 |
|------|------|
| 1 | 注册企业微信 → 开通微信客服（如果还没有） |
| 2 | 在**微信客服后台** → 客服账号 → 创建客服账号，记下**账号名称** |
| 3 | 企业微信后台 → 我的企业 → 企业信息 → 复制 **CorpID** |
| 4 | 微信客服后台 → 开发配置 → 企业内部接入 → 开始使用 → 进入回调配置 |
| 5 | 在 AstrBot 管理面板（或 `cmd_config.json`）填写 corpid / token / encoding_aes_key / kf_name，保存 |
| 6 | 微信客服回调配置页 → **回调 URL** 填 `http://<公网IP>:6199/callback/command`（统一 Webhook）或 `http://<公网IP>:6195/callback/command`（独立端口） |
| 7 | 微信客服回调配置页点击「随机获取」生成 Token 和 EncodingAESKey → 确保与 AstrBot 中填写的一致 |
| 8 | AstrBot 保存配置 → 等待适配器加载完成 → 回到微信客服回调页点击「完成」 |
| 9 | 微信客服后台 → 开发配置 → 获取 **Secret** → 填入 AstrBot 配置 → 再次保存 |
| 10 | 打开 AstrBot 控制台，会显示微信扫码链接 → 扫码后在微信客服聊天中输入 `help` 测试 |

---

## 四、验证连通

AstrBot 控制台日志会输出类似：

```
请打开以下链接，在微信扫码以获取客服微信：
https://work.weixin.qq.com/...
```

扫码后在微信客服对话框输入 `help`，如果收到回复则接入成功。

---

## 五、注意事项

1. **新注册企业的 corpid 可能需约 30 分钟才生效**
2. AstrBot 的 wecom 适配器同时支持**企业微信内部应用**和**微信客服**两种模式，上述配置针对微信客服
3. 如果后续需要 QQ（NapCat）+ 微信客服双通道共存，在 `platform` 数组同时配置 `"wecom"` 和 NapCat websocket 插件即可
4. `emily_agent` 插件放在 `data/plugins/emily_agent/` 后，AstrBot 会自动加载（`data/` 目录已通过 docker volume bind 到容器内 `/AstrBot/data`）
5. **Emily 内核端口映射**：当前 `docker-compose.yml` 中 `emily-core` 的 18080 端口绑定为 `127.0.0.1:18080`（仅本地），如果 `emily_agent` 插件在 AstrBot 容器中通过 HTTP 访问 emily-core，应使用 Docker 内部网络地址 `http://emily-core:18080`（同属 `emily_network`）

---

## 六、相关链接

- [AstrBot 接入企业微信官方文档](https://docs-v3.astrbot.app/deploy/platform/wecom.html)
- [AstrBot 统一 Webhook 模式](https://docs.astrbot.app/use/unified-webhook.html)
