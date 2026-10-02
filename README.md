# WebMonitor-Agent
WebMonitor-Agent：AI 驱动的下一代网页监控引擎。支持 Agent 对话生成脚本，深度解析静态及 React/Vue 动态页面接口，实现全自动化定时任务与数据监控。

## 本地运行环境

当前仓库处于开发规划阶段，尚无可启动的 API、Web、Worker 或数据库迁移。以下配置提供真实可用的 Python 开发环境和 PostgreSQL，不代表业务系统已经实现。

### 宿主机：Conda

安装 Conda 与 Docker Desktop，启动 Docker Desktop 后执行：

```sh
conda env create --file environment.yml
conda activate webmonitor-agent
python -m playwright install chromium
```

环境使用 Python 3.12，包含 FastAPI、Pydantic、SQLAlchemy、asyncpg、Playwright、S3 客户端、OpenAI Agents SDK（`openai-agents==0.22.3`）、官方 MCP Python SDK（`mcp==2.2.0`）和开发工具。OpenAI 基础 SDK 联合锁定为 3.23.0。Linux 宿主机如缺少浏览器系统库，使用 `python -m playwright install --with-deps chromium`（可能需要管理员权限）。Node.js / Next.js 工具独立安装，当前没有前端项目。

已有环境更新依赖：

```sh
conda activate webmonitor-agent
python -m pip install --require-hashes -r requirements-dev.txt
python -m playwright install chromium
```

两个 SDK 均来自正式发布包，不从 `reference/` 加载。Agents SDK 的导入名是 `agents`；MCP v2 使用 `from mcp.server import MCPServer` 和 `from mcp import Client`，Python 结构化结果属性为 `structured_content`。官方 MCP 源码参考位于 `reference/python-sdk/`，技术边界见 [参考实现对齐](development-plan/08-reference-alignment.md)。

可检查安装版本及依赖一致性：

```sh
python -c "from importlib.metadata import version; print({p: version(p) for p in ('openai-agents', 'mcp', 'openai')})"
python -m pip check
```

环境已在 Conda 和 Docker 中验证 Agents 函数工具执行、MCP Streamable HTTP 真实调用和 Agents-to-MCP 桥接。该检查无需模型密钥，不等于验证了外部模型服务或实现了业务 MCP API。

### 本地数据库

首次使用时将 `.env.example` 复制为 `.env`（已有文件不要覆盖），将 `POSTGRES_PASSWORD` 改为随机密码，再启动：

```sh
docker compose up --wait postgres
```

PostgreSQL 16 默认仅监听 `127.0.0.1:5433`，数据库和用户均为 `webmonitor`，数据保存在 Docker 命名卷。端口通过 `POSTGRES_PORT` 调整；宿主机连接时同步调整 `PGPORT`。`.env` 不提交 Git，也不会进入镜像构建上下文。

Compose 自动读取 `.env`，普通 Python 进程不会。宿主机代码可通过 `python-dotenv` 的 `load_dotenv()` 读取，再将 `POSTGRES_PASSWORD` 显式传给 asyncpg 的 `password` 参数；其他连接参数采用 `.env` 中的 `PGHOST`、`PGPORT`、`PGDATABASE`、`PGUSER`。不将密码拼接到 URL，避免特殊字符转义问题。

数据库卷初始化后修改 `.env` 不会修改现有数据库密码，需要用 SQL 修改对应角色密码。停止服务使用 `docker compose down`，保留数据卷；不要随意使用会删除数据的 `down -v`。

### Docker：Python，不安装 Conda

```sh
docker compose --profile dev build dev
docker compose --profile dev run --rm dev
```

`deploy/Dockerfile` 使用官方 Python 3.12 slim 镜像、pip 和同一份带哈希的依赖锁文件，预装 Chromium 及其系统依赖，以非 root 用户运行。`dev` 是交互式 Python 开发容器，不是后台应用服务；也可执行 `docker compose --profile dev run --rm dev bash` 进入 Shell。

容器将仓库挂载到 `/workspace`，数据库地址自动改为 `postgres:5432`，密码通过 `PGPASSWORD` 传入。Linux 宿主机如需写入挂载目录，可在 `run` 时传 `--user "$(id -u):$(id -g)"`。模型密钥可在 `.env` 中设置 `OPENAI_API_KEY`，不应写入代码或镜像。

此 Compose 仅用于可信本地开发：数据库使用初始化角色，开发容器能读写仓库且有外网访问权限；不作为不可信页面采集的生产隔离方案。按规划不引入 Redis，也不配置尚未实现的应用服务、对象存储服务或邮件服务。

### 更新依赖锁

`pyproject.toml` 是依赖声明的唯一来源，`uv.lock` 锁定解析结果，`requirements-dev.txt` 是供 Conda/pip 和 Docker 共用的含哈希导出。无需为宿主机创建额外的 uv 虚拟环境。维护依赖时使用 uv（本次生成版本为 0.12.13）：

```sh
uv lock --python 3.12
uv export --locked --all-groups --no-emit-project --format requirements-txt --output-file requirements-dev.txt
```

升级锁定版本时使用 `uv lock --upgrade --python 3.12`，重新导出后同步更新 Conda 环境并重新构建镜像。当前项目不打包安装，也没有虚构的 CLI 入口。
