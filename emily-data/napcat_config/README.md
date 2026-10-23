# NapCat 配置模板

## 实际配置在哪

真实配置**不在仓库内**，位于与代码仓库同级的运行态目录：

```
D:\app\Emily-var\napcat_config\        （服务器上为 <代码目录>/../Emily-var/napcat_config/）
```

compose 把它挂到容器的 `/app/napcat/config`。本目录只保留 `.example.json` 模板，
用于新环境接入时照抄。

## 文件名规则

NapCat 按文件名识别账号，`ACCOUNT` 处填 QQ 号：

| 模板 | 实际文件名 | 用途 |
|---|---|---|
| `napcat.example.json` | `napcat.json` | 全局客户端设置（日志、hook）。**不含密钥** |
| `napcat_ACCOUNT.example.json` | `napcat_<QQ>.json` | 单账号客户端设置。**不含密钥** |
| `napcat_protocol_ACCOUNT.example.json` | `napcat_protocol_<QQ>.json` | 协议层网络配置。**不含密钥** |
| `onebot11_ACCOUNT.example.json` | `onebot11_<QQ>.json` | OneBot11 连接配置，**含连接 token** |

另有 `passkey.json`、`webui.json`（WebUI 密码）、`napcat_qrcode.png` 由 NapCat 首次启动生成，
同样只落在 `Emily-var`，不入库。

## 新环境接入步骤

1. 在 `Emily-var/napcat_config/` 下复制模板并改名为实际文件名（`ACCOUNT` 换成 QQ 号）
2. 填 `onebot11_<QQ>.json` 的 `websocketClients[].token`，与 `.env` 中 Emily 侧约定的 token 一致
3. 启动 napcat 容器，扫码登录；登录态写入 `Emily-var/napcat_qq/`

## 安全提示

历史提交里曾包含真实的 `onebot11_*.json`（含 token）。这些文件已从仓库移出，
但**旧提交仍可检出**，因此这些 token 应视为已泄露、需要更换，并考虑清理 git 历史。
