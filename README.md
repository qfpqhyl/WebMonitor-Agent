# WebMonitor-Agent

通过对话创建网页监控：真实页面分析、配置草稿、实际提取与邮件预览，经用户确认后定时采集；保留变化事件、运行记录和受权限保护的证据。

**这是本机开发部署，不是生产部署。** PostgreSQL 和 MinIO 数据使用独立 tmpfs；重启 API/Worker 不清数据，停止、重启或重建数据容器会丢失本轮开发数据。旧 `webmonitor-agent_postgres-data` 命名卷保留，但新部署不使用它。

## 启动

需要 Docker Engine 与支持 `additional_contexts: service:...` 的 Docker Compose。Python、Chromium、Node 22 与前端构建均在镜像中完成；正常启动无需宿主 Node。

1. 没有 `.env` 时才从 `.env.example` 复制；已有文件不要覆盖。
2. 配置 `POSTGRES_PASSWORD`、模型网关和 SMTP。邀请注册还需要显式设置逗号分隔的 `REGISTRATION_ALLOWED_EMAILS`。
3. 在仓库根运行：

```sh
docker compose up --build --wait
```

打开 **http://localhost:8080**。只有 Nginx 向宿主发布端口，且绑定 `127.0.0.1`；API、数据库、对象存储和采集代理不直接暴露。

根 **`compose.yaml` 是本项目所有 Docker 服务的唯一基础管理入口**。`compose.dev.yaml` 与 `compose.smoke.yaml` 只叠加开发或验收配置，不依赖手工 `docker run` 启动的辅助服务。

首次创建管理员：

```sh
docker compose exec api webmonitor admin-create --email <管理员邮箱>
```

命令交互读取并确认 12–128 字符密码。不会产生固定密码、默认管理员、演示监控或默认通知组。

签发邀请：

```sh
docker compose exec api webmonitor invite-create --email <允许名单中的邮箱> --ttl-hours 24
```

邀请链接只输出一次，应通过可信方式交付；不要写进日志。注册必须使用邀请对应邮箱，普通注册只获得 `member` 权限。邀请和会话在数据库仅保存令牌摘要。注册页读取邀请后立即清除 URL 中的 token，刷新后需重新打开原链接。

## 配置与凭据边界

配置示例见 `.env.example`。不要向前端传入模型、SMTP、数据库或对象存储私钥。

- `APP_ORIGIN` 默认 `http://localhost:8080`；状态变更检查精确 Origin 和签名 CSRF token，包括登录与注册。更改 `WEBMONITOR_HTTP_PORT` 时也应同步更改 `APP_ORIGIN`。
- 本地 HTTP 必须显式启用 `DEVELOPMENT_MODE=true`，Compose 已设置。HTTPS 下会话 Cookie 使用 Secure；始终 HttpOnly、SameSite=Lax。
- 模型使用 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`，通过 OpenAI-compatible **Chat Completions** 接入。容器访问宿主网关使用 `host.docker.internal`；只有配置的主机为 `127.0.0.1`/`localhost` 时应用才转换主机部分，路径不变。可用 `OPENAI_CONTAINER_BASE_URL` 显式覆盖。
- SMTP 使用 `SMTP_*` 与 `MAIL_FROM`；SSL 和 STARTTLS 必须二选一，证书校验不关闭。`MAIL_TEST_TO` 只供验收，不会自动变成产品收件人。
- `init-db` 从开发 secret 卷生成随机、0600 权限的分角色数据库凭据、证据存储凭据和签名密钥。API、Agent、Scheduler、Collector、Mailer 使用不同 PostgreSQL 登录角色。
- Collector 无权读取用户密码、Session、Invitation、Approval 或 AgentCheckpoint，也不能创建 Monitor/MonitorVersion；通知组与收件人仅有 SELECT 权限。
- 模型密钥只给 Agent，SMTP 凭据只给 Mailer；Browser 不接收这些密钥或数据库初始化凭据。
- MinIO root 凭据只给 S3 与初始化进程。可设置独立的 `S3_ROOT_USER`/`S3_ROOT_PASSWORD`；本地未设置 root password 时复用 `POSTGRES_PASSWORD`，运行证据身份仍独立随机生成且仅能访问专用 bucket。
- 不给应用注入 IMAP 凭据。可选收件箱验收应另行只读执行，且必须区分 SMTP 接收与目标收件箱实际收到。

## 已实现的业务边界

### 创建与审批

工作台与监控列表使用同一对话入口，不要求用户手填 CSS。模型只能基于实际分析提出字段、规则、覆盖范围和采集方式；现价与原价不明确时需要澄清。

草稿使用严格 schema；修改产生新 revision 并作废旧预览与审批。预览实际执行采集、证据上传、规则模拟、收件人去重与默认模板渲染，有效期 15 分钟。所有五类默认模板绑定必须来自服务端目录。

创建工具的 SDK 审批状态不能代替权限：真正创建仅经过 `services.monitors.create_monitor_task`，重新校验成员、版本、预览摘要、通知路由、出口策略和配额，并事务性消费审批。确认秘密只注入可信运行上下文，不写入模型历史或 checkpoint。重复相同幂等请求返回同一任务，键重用于不同参数返回冲突。

Agents SDK 固定为 0.22.3，使用单 Agent/Runner，不实现第二套工具循环。模型执行最多 12 轮、180 秒、单次输出 4096 token，页面上下文有界；外部 tracing 默认禁用。应用额外检查真实流终止原因和完整工具 JSON，截断输出不释放工具调用。PostgreSQL 保存 SDK 历史、版本化 checkpoint 与严格递增事件序号；断开 SSE 不取消执行，等待审批不占 Worker，API/Agent 重启后可恢复。

### 采集与变化

- HTTP HTML 与真实 Playwright Chromium 共用提取、校验、差异与提交逻辑。
- 字段支持 text、number、money、date、boolean、url、list；仅 CSS 选择器和固定浏览器步骤，不执行用户/模型提供的 JS、XPath 或表达式。
- 数字使用 Decimal 和显式分隔符；货币、日期格式/时区、布尔匹配值必须明确。缺失不等于空字符串，重复业务键、历史非空变空或类型异常均失败。
- 浏览器步骤仅限有界等待、滚动和翻页；iframe/Shadow DOM 不提取并报告限制。无法证明全量时显示 bounded。列表 full 覆盖需要可核对的声明总数与完整位置证据（`aria-setsize`/`aria-posinset`）；翻页或虚拟列表未证明完整时不能生成删除结论。
- 支持文本变化、关键词状态、数字绝对/百分比变化、阈值，以及列表新增/删除/更新。规则仅一层 all/any。
- 百分比参考**上次成功采集**，不是累计参考价；前值为零记录 `rule_undefined`。数字仅显示格式变化不产生数值更新事件。
- 首次成功建立基线，不发业务变化；此后每次成功均推进基线。持续状态只在 false→true 通知，变化类不会吞掉连续不同列表新增。
- 调度间隔 60–86400 秒；晚到周期合并，不无限补发。工作区最多 100 个 active 监控，每用户最多一个活动 AgentRun。

采集必须经过 Squid 实际连接 ACL，拒绝私网、loopback、link-local、metadata、异常端口与不安全重定向；采集容器只有 internal 控制网络。HTTP 限 5 MiB/30 秒/5 次重定向，压缩输出在展开分配前受限。浏览器为全新 context、UID 10001、`chromium_sandbox=True`、只读根文件系统、cap_drop ALL；限制 60 秒、100 请求、20 MiB、5 MiB DOM，数据库跨进程浏览器并发最多 2。沙箱不可用返回 `browser_unavailable`，不降级成无沙箱。

公网 DNS 由同一 `egress` 容器内的 Unbound 通过验证证书的 DNS-over-TLS 转发给 Cloudflare/Quad9；这项外部解析已获用户同意。采集容器不做直连公网 DNS，也不放行宿主 VPN 的 198.18.x.x Fake-IP。Squid 对解析后的实际连接地址执行 ACL，HTTPS CONNECT 仅允许443。仅 smoke 配置将精确的 `fixture.` 名称交给 Docker DNS，其余名称仍使用加密解析。

Playwright seccomp 基于[官方 Docker profile](https://github.com/microsoft/playwright/blob/v1.63.0/utils/docker/seccomp_profile.json)，增加用户 namespace 内 Chromium 所需的 `chroot` 调用许可；仍由内核检查 namespace capability，不给容器增加 capability。

### 运行、证据与通知

Run/Attempt 使用 90 秒租约、15 秒续租与 generation fencing；提交锁定监控并比较状态、版本和基线。暂停/取消使旧结果无法提交。仅临时网络/存储故障最多重试三次，等待 1/5 秒；契约失败不盲重试。失败保留可信基线、错误码和可得证据；同一未恢复故障不反复发邮件，恢复后与最后可信基线比较。

S3 上传成功后才提交证据引用。证据接口检查工作区；HTML 以 `text/plain` 附件返回，不作为同源页面执行，截图为 PNG。快照、基线、事件与 Outbox 同事务提交。

管理员管理显式邮箱通知组；成员只读选择已有组。两个组选中同一邮箱只创建一条 Delivery，并冻结原成员来源。投递前重新校验至少一个原来源仍有效；新增成员不补发历史邮件，全部原来源撤权则 cancelled。

五类默认模板为 price_changed、list_changed、content_changed、run_failed、recovered，随 wheel 分发，StrictUndefined + 自动转义。价格模板缺 `current_price` 会拒绝预览，不静默换模板。邮件预览使用清洗后的无脚本 sandbox iframe。

`sent` 只表示 SMTP 接收。Message-ID 稳定，临时错误最多三次、60/300 秒退避，永久错误 failed；SMTP 接收后数据库提交丢失仍可能重发，不承诺 exactly-once。

通知路由更改必须重新真实预览并显式确认，发布不可变 MonitorVersion，不直接修改当前版本。

**本次不提供自动修复、AI 模板生成/编辑/发布或外部 MCP。** 无空按钮、假成功入口；`/mcp` 与认证发现元数据路径返回 404。

## 服务与运行命令

| 服务 | 职责 |
| --- | --- |
| proxy / web / api | 同源入口、Next.js 页面、账户与领域 API |
| agent-worker | 模型、工具、审批恢复与持久化事件流 |
| scheduler | 到期扫描、合并周期与排队 |
| http-worker / browser-worker | 隔离采集、证据、fenced 提交 |
| mailer | 授权重查、模板渲染与真实 SMTP |
| postgres / s3 | 本轮开发数据与私有证据 |
| egress | 页面请求唯一出口 |
| init-db | 单次 schema 校验、工作区/模板、最小权限与存储初始化 |
| dev（profile） | 可信交互开发与隔离回归测试 |

```sh
docker compose ps
docker compose logs api agent-worker scheduler http-worker browser-worker mailer
curl --fail http://localhost:8080/api/v1/health/live
curl --fail http://localhost:8080/api/v1/health/ready
# 只重启应用，保留当前数据库、证据和待确认审批
docker compose restart api agent-worker
```

readiness 实际检查 PostgreSQL、schema 指纹、初始化状态；未初始化或不匹配返回 503，不自动迁移/删表。Worker 在 PostgreSQL 写心跳，不提供假的 HTTP 健康端点。

`docker compose stop` 也会停止数据容器并丢弃 tmpfs。需要有意重建本轮开发数据时，先确认无需保留，再重新创建数据服务并运行初始化：

```sh
docker compose up -d --force-recreate postgres s3
docker compose up --force-recreate --no-deps init-db
docker compose up -d --wait
```

不要执行 `down -v` 或删除旧 `postgres-data`。需要长期数据保留时必须另行设计持久部署，不能把当前 tmpfs 当生产存储。

MinIO 原指定公共镜像在本环境无法拉取，经用户批准使用 `deploy/minio.Dockerfile` 从[同一官方 Release](https://github.com/minio/minio/releases/tag/RELEASE.2025-04-22T22-12-26Z)构建本地镜像；二进制 SHA-256、架构与来源在 `deploy/minio-artifacts.json`，不是内存/本地文件替代品。

## 开发与测试

```sh
# 可选：源码只读挂载，以及仅宿主 localhost:5433 的数据库端口
# browser-worker 仍使用构建镜像，不挂宿主源码
docker compose -f compose.yaml -f compose.dev.yaml up --build --wait

# 交互开发
docker compose --profile dev run --rm dev

# 创建随机隔离测试库，初始化后跑项目回归，最后只删除自己创建的测试库
docker compose --profile dev run --rm dev pytest
```

项目测试仅发现 `tests/unit` 与 `tests/integration`，不执行 `reference/` 中第三方项目的测试。覆盖百分比零基线、连续新增、数字表示等价、邀请并发、审批改版、过期租约/暂停 fencing、失败保留基线、收件人撤权、SDK 合成 ID、截断工具响应、压缩预算与覆盖证明等边界。

宿主验收工具可用 uv 安装，不修改全局 Python/Node：

```sh
uv sync --locked --python 3.12
uv run python -m playwright install chromium
```

依赖统一维护：

```sh
uv lock --python 3.12
uv export --locked --all-groups --no-emit-project --format requirements-txt --output-file requirements-dev.txt
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file requirements.txt
uv build --wheel
```

前端使用 Node 22 与唯一 `web/package-lock.json`；`npm run generate:api` 从运行中的 `/api/v1/openapi.json` 生成 `web/src/lib/api/schema.d.ts`。构建/typecheck 不能替代真实业务验收。

## 可重复真实验收

只启用验收 overlay 时允许精确目标 `http://fixture:8000`，其他私网仍拒绝。fixture 不向宿主发布管理端口，React/Vue 资源本地提供，不依赖 CDN。

```sh
export SMOKE_TOKEN="$(openssl rand -hex 32)"
docker compose -f compose.yaml -f compose.smoke.yaml up --build --wait

# 默认只实际分析、提取与预览，不确认生产监控、不发生产邮件
uv run python scripts/smoke_mvp.py --base-url http://localhost:8080

# 明确允许完整闭环与真实邮件；收件人只能为配置的 MAIL_TEST_TO
uv run python scripts/smoke_mvp.py --base-url http://localhost:8080 --send-mail
```

**`--send-mail` 会发送多封真实邮件，涵盖变化、故障和恢复；重新运行还会再次发送。** 请先使用默认的仅预览模式。需要停止发信时运行 `docker compose stop mailer`；已经被 SMTP 接收的邮件无法撤回，可能稍后到达。

脚本交互读取已创建管理员的邮箱/密码，创建两个重叠收件人的验收组，通过真实模型对话生成配置；模型不可用会失败退出，不回退到手工配置。完整模式验证静态/SSR/React/Vue、确认前不创建、重启恢复与幂等、首次基线静默、100→80、连续 B/C 新增、重复键/空值/故障不改基线、私网拦截、证据权限和撤权取消投递。管理修改通过同项目 `docker compose exec -T fixture` 完成，管理 token 不输出。

截图保存在 `.smoke-artifacts`，含工作区数据，按私有验收材料处理。脚本结束暂停本次创建的监控，保留运行与证据；收件箱实际送达仍需独立确认。邀请注册、成员权限与账号安全测试另按真实邀请流程及隔离集成测试执行。
