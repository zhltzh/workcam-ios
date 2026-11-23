# WorkCam W009 受控内网部署方案

**仅供受控内网联调——不宣称生产就绪、不开放公网。**

## 1. 环境变量模板

复制 `tools/deploy/env.template` 为 `.env`（或直接在启动脚本中设置）：

```bash
# 业务数据库（必填）
WORKCAM_BUSINESS_DB=/srv/workcam/data/business/workcam.db

# 设备注册码（受控注册——必填；留空 = 注册关闭）
WORKCAM_ENROLLMENT_CODE=<现场生成的随机串>
# 或 WORKCAM_ENROLLMENT_CODE_FILE=/srv/workcam/secrets/enrollment-code.txt

# 实验上传端点（联调期需要则设 1；生产严禁）
# WORKCAM_UPLOAD_EXPERIMENTAL=1

# 上传鉴权（远程客户端必须开）
WORKCAM_UPLOAD_AUTH=1

# 反向代理场景（见 §5 剩余要求）
# WORKCAM_TRUST_PROXY_HEADERS=1

# 旧文件注册表（W009 前的联调设备——留空用默认路径）
# WORKCAM_DEVICE_REGISTRY=...
```

## 2. 启动前配置校验

启动时自动执行（`security_config.check_startup_security`）：

- `WORKCAM_ENV=production` + `UPLOAD_EXPERIMENTAL=1` → **拒绝启动**（exit 1）
- 其余危险/可疑组合打印 stderr 警告

手动预检：`python -c "from app.services.security_config import check_startup_security; check_startup_security()"`

## 3. 数据库初始化与迁移

**零手工操作**：uvicorn 启动时自动执行幂等迁移（`PRAGMA user_version` 逐级前进，当前 v5）。

验证：

```bash
sqlite3 $WORKCAM_BUSINESS_DB "PRAGMA user_version;"   # 应为 5
```

## 4. 健康检查与优雅关闭

```bash
# 健康
curl http://127.0.0.1:8000/health
# → {"status": "ok", "service": "workcam-verify"}

# 业务就绪（迁移完成）
curl http://127.0.0.1:8000/api/v1/projects -H "X-Device-Token: ..."  # 200/空列表

# 优雅关闭：SIGTERM/SIGINT（uvicorn 内建——完成在途请求后退出）
```

## 5. 备份与恢复演练

### 备份（数据库 + 原图 + 身份，三部分）

```bash
# ① 数据库（VACUUM INTO 一致性备份——含 v5 身份表）
python -c "
from app.db import backup_to, verify_backup
from pathlib import Path
backup_to(Path('/srv/workcam/backup/workcam-$(date +%F).db'))
print(verify_backup(Path('/srv/workcam/backup/workcam.db')))  # integrity + FK 审计"

# ② 原图对象（W007 带复验的 manifest 备份）
python -c "
from app.services.original_store import backup
backup(Path('/srv/workcam/backup/originals-$(date +%F)'))"
```

### 恢复演练（服务重启一致性已测试钉死）

```bash
systemctl stop workcam            # 或 Ctrl+C
# 恢复 DB 到新路径（拒绝覆盖非空库）+ 原图 restore（幂等、损坏拒覆盖）
# 更新 WORKCAM_BUSINESS_DB 指向恢复路径 → 启动
systemctl start workcam
# 验证：身份可解析、项目/照片/同步游标一致（见 test_backup_restore_with_identity）
```

## 6. 受控内网拓扑

```
iPhone ──(内网 Wi-Fi/VPN)── [反向代理: TLS 1.3] ── WorkCam uvicorn
                              │  - 剥离客户端 XFF
                              └─ - 只转发到 127.0.0.1:8000 或内网 IP
```

**启动建议**：`--host 127.0.0.1`（代理同机）或内网网卡 IP；**绝不** `0.0.0.0` 直接暴露。

## 7. 剩余要求（生产差距——如实）

| 项 | 要求 | 当前状态 |
|---|---|---|
| TLS | 反向代理终止（Let's Encrypt / 内部 CA） | 未实现（联调用 HTTP） |
| 反代配置 | 剥离客户端 XFF；只追加真实 peer | 文档要求；未部署验证 |
| 密钥管理 | 注册码/管理员码进 secret 管理（Vault/KMS） | 环境变量/文件（联调级） |
| 公网部署 | **本轮不做** | N/A |
| 管理员 RBAC | 操作员角色体系 | 注册码兼管理员（MVP 简化） |
| 审计防篡改 | WORM/外部锚定 | 应用日志（可删改——声明在案） |
| 数据库 HA | PostgreSQL + 副本 | SQLite 单写者（内网单实例足够） |
