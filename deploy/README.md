# Emily 服务器部署（ECS / 单机 Docker）

本文对应 **仅微信小程序渠道** 的部署：`emily-core` + `emily-postgres` + `wechat-gateway`。
不含 AstrBot、NapCat、mitmproxy（需要时再启用 `docker-compose-napcat.yml` 等开发编排）。

## 1. 目录约定

```
/srv/emily/                 # 代码（git clone 到这里，目录名随意）
/srv/Emily-var/             # 运行态数据，与代码目录同级（名字固定）
    ├─ knowledge_library/   # 业务资料库（需另传）
    ├─ attachments/ session_archives/ logs/ journal/ notebooks/
    ├─ user_memory/ runtime/ evolution/
    └─ wechat-gateway-uploads/
```

- 运行态**不在仓库内**：`rsync --delete` 同步代码不会误删数据
- 数据库用 Docker 命名卷 `emily_pgdata`，不在 `Emily-var` 里；备份走 `pg_dump`
- 若代码目录名不是 `Emily`，路径不受影响（一律用 `../Emily-var` 相对解析）；
  想换位置就在 `.env` 里设 `VAR_DIR=/绝对路径`

## 2. 前置条件

- Docker + Docker Compose v2（`docker compose version` 可用）
- 出网：构建镜像需要访问阿里云 apt 源与 OfficeCLI 下载站（`emily-core/Dockerfile`）
- 域名已解析到本机，证书就绪（小程序强制 HTTPS + ICP 备案）
- 服务器端口：仅需放行 80/443；18080/18090 只绑本机，由 nginx 反代

## 3. 首次部署

```bash
# ① 取代码（浅克隆即可，不需要完整历史）
git clone --depth 1 git@github.com:Ellyshome/Emily.git /srv/emily
cd /srv/emily

# ② 准备 .env（必填：POSTGRES_PASSWORD / EMILY_API_TOKEN / EMILY_LLM_API_KEY）
cp .env.example .env
vi .env

# ③ 初始化：检查配置 → 建 Emily-var → 构建镜像 → 起库和 core → 应用业务种子 SQL
bash deploy/prepare.sh

# ④ 创建首个管理员（--wechat 填小程序侧上报的用户标识，可先 --list 查看）
docker compose -f docker-compose.server.yml exec -T emily-core \
  python /app/deploy/bootstrap_admin.py --username 管理员 --level 6 --wechat <微信用户标识>

# ⑤ 启动全部服务
docker compose -f docker-compose.server.yml up -d
```

`.env` 关键项说明：

| 变量 | 必填 | 说明 |
|---|---|---|
| `POSTGRES_PASSWORD` | ✅ | 数据库密码，**只用字母数字与 `-_`**（含 `:@/` 会破坏连接串） |
| `EMILY_API_TOKEN` | ✅ | Core API 鉴权令牌，同时用于网关调用 Core |
| `EMILY_LLM_API_KEY` | ✅ | LLM 密钥 |
| `SILICONFLOW_API_KEY` | 建议 | Embedding 与 VLM 共用 |
| `EMILY_KB_ENABLED` | 可选 | 启用 pgvector RAG 时设 `true` |
| `VAR_DIR` | 可选 | 运行态根，默认 `../Emily-var` |
| `EMILY_RELEASE` | 可选 | 镜像 tag，默认 `latest`（发布时建议设为版本号） |

## 4. 配置 nginx

```bash
export EMILY_DOMAIN=example.com
export SSL_DIR=/etc/nginx/ssl/emily
envsubst '${EMILY_DOMAIN} ${SSL_DIR}' \
  < deploy/nginx/emily.conf.tmpl > /etc/nginx/conf.d/emily.conf

# 控制台 Basic Auth
printf "admin:$(openssl passwd -apr1 '改我')\n" | sudo tee /etc/nginx/.emily_htpasswd
chmod 640 /etc/nginx/.emily_htpasswd

nginx -t && systemctl reload nginx
```

## 5. 验证

```bash
# Core 健康（令牌从 .env 取）
curl -s -H "X-Emily-Token: $(grep -E '^EMILY_API_TOKEN=' .env | cut -d= -f2-)" \
     http://127.0.0.1:18080/api/v1/health

# 网关健康
curl -s http://127.0.0.1:18090/health

# 容器健康状态
docker compose -f docker-compose.server.yml ps
```

## 6. 日常运维

```bash
# 数据库备份（建议加到宿主机 cron）
docker exec emily-postgres pg_dump -U emily emily > /srv/backup/emily_$(date +%F_%H%M).sql

# 备份运行态数据（附件/归档/资料库，体积较大）
tar czf /srv/backup/emily_var_$(date +%F).tgz -C /srv Emily-var

# 更新代码并重建
git pull
docker compose -f docker-compose.server.yml build emily-core wechat-gateway
docker compose -f docker-compose.server.yml up -d

# 查看日志（已配置轮转：core 20m×5，其余 10m×3）
docker compose -f docker-compose.server.yml logs -f --tail=100 emily-core
```

业务种子 SQL 有台账（`schema_migrations` 表），`deploy/prepare.sh` 可重复执行，已应用项会跳过。
新增登记类 SQL 后，重跑 prepare 即可增量应用。

## 7. 安全须知

1. **网关身份是假的**：`wechat-gateway` 用 `X-Dev-User` 头识别用户，未接
   `wx.login/code2session`。公网暴露前必须至少加 Basic Auth 或 IP 白名单
   （模板里已留注释位），根治方案是改造为真实微信登录。
2. **控制台必须保留 Basic Auth**：`/console` 无内置登录，模板已启用。
3. **NapCat token 曾进过 git 历史**（已从历史剥离），历史的 token 应视为泄露并更换。
4. 后端端口全部绑 `127.0.0.1`，不要改成 `0.0.0.0` 绕过 nginx。

## 8. 当前未覆盖（后续补齐）

- **Linux 备份脚本**：目前只有 Windows 的 `scripts/backup.ps1`；本文第 6 节给了等价命令，
  尚未脚本化 + 保留策略 + 恢复演练
- **镜像仓库与回滚**：现在是服务器本地构建，建议后续推私有镜像仓并按 `EMILY_RELEASE` 打 tag
- **监控告警**：无（可接宿主机 node_exporter / 云监控）
- **AstrBot / NapCat 渠道**：未纳入本编排，需要时参考 `docker-compose-napcat.yml`
- **测试用例页**：`/app/test_cases` 挂载已移除（`Issues/` 已迁出仓库），控制台该页为空属预期
