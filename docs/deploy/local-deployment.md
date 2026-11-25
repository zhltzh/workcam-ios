# WorkCam 后端本地部署指南（Windows / macOS）

任务 B02-W027 · 基线 `31917e3` · 分支 `dev/windows-b02-w027-deploy-ci`

本文档覆盖：环境初始化、依赖锁定、环境变量、数据库初始化与迁移演练、启动（dev / 生产式）、健康检查、日志、一键测试、备份恢复、Docker、CI 配置、dev/prod 隔离。

> 受控内网部署（TLS/反代拓扑）见 `docs/mvp/w009-deployment.md`，本文不重复。
> **红线**：本服务只做本地/受控内网验证，绝不对公网开放。

---

## 1. 前置条件

| 平台 | 需要 |
|---|---|
| Windows | PowerShell 5.1+，Python 3.12+（`py -V:3.12` 或 PATH 中的 python） |
| macOS / Linux | Python 3.12+（`brew install python@3.12` 或系统包管理器），bash |

无其他系统依赖：数据库是 SQLite（WAL 模式，随 Python 附带），无外部服务。

## 2. 环境初始化（bootstrap）

`bootstrap.ps1` / `bootstrap.sh` 完成：Python 3.12+ 探测 → 在 `backend/.venv` 创建虚拟环境 → 安装依赖。

```powershell
# Windows（需要代理时；无代理环境去掉 -Proxy）
cd backend
powershell -NoProfile -ExecutionPolicy Bypass -File bootstrap.ps1 -Proxy http://127.0.0.1:10809
```

```bash
# macOS / Linux
cd backend
./bootstrap.sh            # 默认 requirements.txt（宽松版本约束）
./bootstrap.sh --locked   # 精确版本（requirements.lock.txt）
```

真实输出（Windows，2026-11-25，venv 已存在时幂等复用）：

```
[bootstrap] using py -V:3.12
[bootstrap] .venv already exists - reusing it
[bootstrap] installing from requirements.txt
Requirement already satisfied: ...
[bootstrap] done. Next steps: ...
```

## 3. 依赖锁定

| 文件 | 角色 |
|---|---|
| `requirements.txt` | **意图声明**：宽松约束（`fastapi>=0.115.0` 等）。日常开发与 CI 用它，能持续发现新兼容版本。 |
| `requirements.lock.txt` | **精确复现**：`pip freeze` 产出，锁定全套 28 个直接+传递依赖的精确版本。生产式部署与 Docker 用它。 |

再生成 lock（必须在新 venv 中，勿手改）：

```bash
python -m venv .venv && .venv/Scripts/python -m pip install -r requirements.txt   # 或 .venv/bin/python
.venv/Scripts/python -m pip freeze > requirements.lock.txt
```

验证记录：本机 `pip install --dry-run --ignore-installed -r requirements.lock.txt` 全部 28 包可解析（`Would install ...`，exit 0）。
注意：`colorama` 是 pytest 的 Windows 专属传递依赖，macOS/Linux 上装它无害但非必需。

## 4. 环境变量（模板 + 安全等级）

三个模板（均为占位符，已加入 `.gitignore` 例外，真实 env 文件被忽略）：

| 模板 | 用途 |
|---|---|
| `.env.example` | 全量变量说明（每个变量带用途与安全等级标注） |
| `.env.development.example` | 开发模板，数据目录 `data/dev/...` |
| `.env.production.example` | 生产模板，数据目录 `data/prod/...` |

应用从**进程环境变量**读配置（代码里没有 dotenv）。`scripts/start_dev.*` / `scripts/start_prod.*` 启动时会解析 `backend/.env.<env>` 并导出全部 `KEY=VALUE`。

安全等级（详见 `.env.example` 内注）：

- `[SECRET]`：`WORKCAM_ENROLLMENT_CODE`、`WORKCAM_ENROLLMENT_CODE_FILE` —— 脚本生成：`python -c "import secrets; print(secrets.token_urlsafe(32))"`，永不提交、永不写日志
- `[RISK]`：`WORKCAM_UPLOAD_EXPERIMENTAL`（生产环境与 `WORKCAM_ENV=production` 组合会**拒绝启动**，security_config 守卫 G1）、`WORKCAM_UPLOAD_AUTH`、`WORKCAM_TRUST_PROXY_HEADERS`
- `[SAFE]`：`WORKCAM_BUSINESS_DB` 等路径类

dev/prod 数据隔离：两套模板的 `WORKCAM_BUSINESS_DB` 分别指向 `data/dev/business/workcam.db` 与 `data/prod/business/workcam.db`（原图对象存储在 DB 同级的 `originals/` 目录，随之隔离）。`data/` 整体被 `.gitignore` 忽略。

## 5. 数据库初始化与首次启动

迁移链现状（`app/db.py`）：`MIGRATIONS` v1–v8，基于 `PRAGMA user_version`，幂等可重放。**服务首次请求会自动懒迁移**（W013 起：迁移失败返回 503，绝不带病服务）。

显式初始化（可选，便于部署时提前发现迁移问题）：

```powershell
.venv\Scripts\python.exe scripts\db_admin.py init            # 建库 + 迁移到最新
.venv\Scripts\python.exe scripts\db_admin.py status          # 版本 + 表清单
.venv\Scripts\python.exe scripts\db_admin.py migrate          # 应用待办迁移（幂等）
.venv\Scripts\python.exe scripts\db_admin.py migrate --to 7   # 只建到 v7（演练用）
```

首次启动全流程：bootstrap → 复制 env 模板并填值 → `db_admin.py init`（或直接启动，首个请求自动迁移）→ `curl /health` 探活 → 业务端点探活（401 = 鉴权就绪，非 503）。

## 6. 迁移演练记录（2026-11-25 真实执行）

演练目录：`%TEMP%\zhanlu\b02w27-migration-drill`，全部为真实命令与输出。

### 6.1 fresh install → 最新版

```
> .venv\Scripts\python.exe scripts\db_admin.py --db workcam.db init
[init] applied 8 migration(s), user_version=8
> ... status
[status] user_version=8 (v8)
[status] tables: mvp_annotations, mvp_audit_log, mvp_auth_challenges, mvp_derivatives,
 mvp_devices, mvp_evidence_links, mvp_issue_history, mvp_issue_links, mvp_issue_photos,
 mvp_issues, mvp_originals, mvp_photo_originals, mvp_photos, mvp_projects,
 mvp_rate_limits, mvp_reports, mvp_sync_events, mvp_sync_ops
```

### 6.2 逐级迁移（v1 → v7 → v8）

```
> ... db_admin.py --db rollback.db migrate --to 7
[migrate] rollback.db 0 -> 7 (7 applied)          # 逐级 v1..v7，每级独立事务
> ... db_admin.py --db rollback.db migrate
[migrate] rollback.db 7 -> 8 (1 applied)          # 幂等：replay 只应用差集
```

### 6.3 回滚一版（v8 → v7）

迁移链**没有自动 downgrade**（SQLite 迁移不生成逆脚本），回滚的正确姿势是**从备份恢复**：

```
# ① 在 v7 时点做联合备份
> ... db_tools.py --db rollback.db backup --out-dir backup-v7
[backup] db   : backup-v7\workcam.db (pages=43, v7, counts={'projects': 0, ...})
# ② 升级到 v8（此时想退回 v7）
> ... db_admin.py --db rollback.db migrate
[migrate] rollback.db 7 -> 8 (1 applied)
# ③ 回滚 = 移走 v8 库 + 恢复 v7 备份（restore 拒绝覆盖非空库）
> move rollback.db rollback.db.v8-side
> ... db_tools.py restore --backup-dir backup-v7 --target rollback.db
[restore] db   : -> rollback.db (v7, counts={'projects': 0, ...})
# ④ 验证：版本与 v8 新增表全部回到 v7 状态
rolled-back (v7): user_version=7, v8-only tables: (none)
side-moved (v8):  user_version=8, v8-only tables:
 ['mvp_annotations', 'mvp_issue_history', 'mvp_issue_links',
  'mvp_issue_photos', 'mvp_issues']
```

## 7. 启动

### dev（热重载）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_dev.ps1            # 默认 .env.development，8000
powershell ... -Port 8001 -Env production                                            # 换端口/env
```
```bash
./scripts/start_dev.sh
ENV_NAME=production PORT=8001 ./scripts/start_dev.sh
```

内部：加载 `.env.<env>` → `.venv\Scripts\python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --log-level info`。

### 生产式（无热重载，多 worker）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start_prod.ps1                      # .env.production，4 worker
powershell ... -Workers 2 -LogConfig scripts\logging_rotate.json                                 # 轮转日志
```
```bash
./scripts/start_prod.sh
WORKERS=2 LOG_CONFIG=scripts/logging_rotate.json ./scripts/start_prod.sh
```

内部：`uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4`（无 `--reload`）。
**所有启动脚本一律绑 127.0.0.1**；需要远程访问时由受控内网反代转发（见 w009 文档 §6），绝不直接改绑 0.0.0.0。

真实验证（2026-11-25，本机）：
- dev（8001）：`GET /health → 200 {"status":"ok","service":"workcam-verify"}`；`GET /api/v1/projects`（无 token）→ **401**（懒迁移成功，非 503）；`.env.development` 生效，库建于 `data/dev/business/workcam.db` 且 v8
- prod 4-worker（8004）：`/health → 200`；`/api/v1/projects → 401`

## 8. 健康检查

`GET /health` 已存在（`app/api/verify.py:34`），只读、无副作用：

```json
{"status": "ok", "service": "workcam-verify"}
```

另有 `/time-anchor/health`（W004 时间锚子系统，`app/api/time_anchor.py:140`）。
探活顺序建议：`/health`（进程活着）→ 任一业务端点（迁移完成：401=正常鉴权拒绝；**503=迁移失败**，见 `app/main.py` 的 503 中间件）。

## 9. 日志管理

现状：stdlib `logging`（`main.py` 里 `logging.basicConfig(level=logging.INFO)` + `SensitiveFilter` 敏感词脱敏），uvicorn 自带 access/error 日志。**不引入新日志框架**。

- **级别**：启动脚本 `-LogLevel` / `LOG_LEVEL` 透传 uvicorn `--log-level`（debug/info/warning/error）
- **轮转**：`scripts/logging_rotate.json`（uvicorn `--log-config` 的 dictConfig 文件）——应用日志 `logs/workcam-app.log`、访问日志 `logs/workcam-access.log`，`RotatingFileHandler` 10MB × 5 份
  - 真实验证：4-worker 启动后 `logs/workcam-access.log` 写入 `127.0.0.1:xxx - "GET /health HTTP/1.1" 200`
  - 踩坑记录：`uvicorn.access` 的 record 用 **printf 风格位置参数**，formatter 不能引用 `%(client_addr)s` 等命名字段（KeyError → 全部丢弃），必须只用 `%(message)s`
  - 已知限制：多 worker 时各进程写同一文件存在轮转竞争（小流量内部部署可接受）；更稳的方案是 stdout 由 systemd/容器收集轮转
- 敏感信息：`SensitiveFilter` 对含 private_key/secret/token/api_key 的日志脱敏

## 10. 一键测试

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\run_tests.ps1                 # 全量
powershell ... -Target tests\test_api.py                                                  # 单文件
powershell ... -Target "tests/test_api.py::test_health"                                    # 单测
powershell ... -Seed 0                                                                     # PYTHONHASHSEED=0 变体
```
```bash
./scripts/run_tests.sh
./scripts/run_tests.sh tests/test_api.py
./scripts/run_tests.sh --seed 0
```

真实结果（2026-11-25，本机）：
- 全量（无 seed）：**632 passed, 3 skipped**（332.58s）
- 全量（`-Seed 0`）：**632 passed, 3 skipped**（326.22s）
- `-Target tests\test_api.py`：26 passed（0.86s）

seed 变体的意义：W013 曾出现 pytest 哈希随机收集顺序引发的「仅全量跑挂」flake，`PYTHONHASHSEED=0` 用于复现/钉死该类问题（见 `app/main.py` 注释与 `test_w013_r1.py`）。

## 11. 备份与恢复

`scripts/db_tools.py` 包装**既有逻辑**（不重写）：`app.db.backup_to / verify_backup / restore_from`（VACUUM INTO 一致性备份，恢复拒绝覆盖非空库）+ `original_store.backup / restore`（manifest 驱动、逐对象哈希复验）。

```powershell
powershell -File scripts\backup.ps1  -OutDir data\backups\2026-11-25 [-DbPath <db>]   # 备份（DB+原图+manifest.json）
powershell -File scripts\restore.ps1 -BackupDir data\backups\2026-11-25 -Target <新路径>  # 恢复（必须新路径）
.venv\Scripts\python.exe scripts\db_tools.py verify --backup-db <备份文件>              # 单独校验
```
```bash
./scripts/backup.sh OUT_DIR [DB_PATH]
./scripts/restore.sh BACKUP_DIR TARGET_DB
```

备份目录结构：`workcam.db`（一致性快照）+ `originals/`（内容寻址副本）+ `manifest.json`（清单 + 校验信息 + 时间戳）。

### 真实 roundtrip 演练（2026-11-25）

```
[init] applied 8 migration(s), user_version=8
seeded: 1 project row(s)                                   # 直插业务数据模拟线上库
[backup] db   : rt-backup\workcam.db (pages=56, v8, counts={'projects': 1, ...})
# 删除活库（模拟灾难）→ restore.ps1 恢复
[restore] db   : -> roundtrip.db (v8, counts={'projects': 1, ...})
# 校验
user_version=8, projects=[('p-drill-001', 'Drill Site A')]
ROUNDTRIP OK: business data survived
[verify] rt-backup\workcam.db: integrity ok, FK audit clean, v8, counts={'projects': 1, ...}
```

## 12. Docker（本机未验证构建）

交付 `backend/Dockerfile`、`backend/docker-compose.yml`、`backend/.dockerignore`。

**构建未在本机验证（无 Docker Desktop）** —— 本机 `docker --version` 返回「无法识别」。配置要点：

- `python:3.12-slim` + `pip install -r requirements.lock.txt`（精确版本）
- 容器内绑 0.0.0.0（容器网络命名空间内），**宿主侧 compose 只映射 `127.0.0.1:8000:8000`**——绝不改成 `0.0.0.0:8000`
- 数据卷 `./data-docker:/data`，注册码从宿主环境注入（`WORKCAM_ENROLLMENT_CODE`），严禁硬编码
- 使用：`cd backend && docker compose up --build`

待有 Docker 环境的机器执行一次构建/启动/探活后，此节状态方可改为「已验证」。

## 13. GitHub Actions CI（配置交付，未启用远端）

交付 `.github/workflows/ci.yml`：push（任意分支）/ PR 触发，`ubuntu-latest` + Python 3.12（`actions/setup-python@v5`），步骤为 `pip install -r backend/requirements.txt` 与 `backend/` 目录下 `python -m pytest -q`。免费 runner，不涉及付费资源；未配置 Windows 自有 runner（按任务要求）。

**未实际启用**：未 push 到 main、未改远端 repo 设置。本地等价命令已真实验证——CI 的两个核心步骤（`pip install -r backend/requirements.txt`、backend 下 `pytest -q`）即本机 .venv 的构建方式，2026-11-25 两次全量运行均 **632 passed, 3 skipped**。推送到远端仓库后 Actions 面板会自动生效（无需手工开启，如果 repo 默认启用了 Actions）。

## 14. dev / prod 隔离清单

| 层 | 机制 |
|---|---|
| 配置 | `.env.development` / `.env.production` 独立文件，启动脚本按 `-Env` / `ENV_NAME` 选择 |
| 数据 | `data/dev/...` vs `data/prod/...`（DB + originals + 实验上传全部随 `WORKCAM_BUSINESS_DB` 隔离） |
| 安全守卫 | `WORKCAM_ENV=production` 时：实验端点硬禁（+EXPERIMENTAL=1 拒绝启动）、测试公钥注册禁用、缺注册码告警 |
| 日志 | 同机双跑时用不同 `--port`；轮转日志文件按启动目录区分（各自 `logs/`） |
| 泄漏防护 | 真实 env 文件被 `.gitignore`（`.env`、`.env.*`），模板（`*.example`）以否定规则入库 |
