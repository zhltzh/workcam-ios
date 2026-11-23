# WorkCam W009 安全设计

日期：2026-11-23 · 分支 `dev/windows-mvp-w009`（基于 PR #14 HEAD `6a204a4`）

## P0 设备身份（SQLite 持久化）

### 数据模型（迁移 v5）

```
mvp_devices          device_token PK（公钥 sha256）、device_id、
                     public_key_b64、key_fingerprint、status(active|revoked)、
                     enrolled_at、revoked_at
mvp_auth_challenges  nonce PK（单次使用）、device_token、expires_at
mvp_rate_limits      (device_token, window_start) PK 滑动窗口计数
mvp_audit_log        结构化事件 + 指纹脱敏 + 保留期
```

### 受控注册

- 注册需**带外注册码**（`WORKCAM_ENROLLMENT_CODE` 环境变量或文件）——任意客户端不能自注册为可信设备
- 无注册码时注册**关闭**（默认安全姿态）
- 注册码错误 → 403（不提示码是否存在）
- 吊销后重注册 → 409 `device_revoked`（需人工介入）
- 同公钥重注册 → 幂等（同 token）

### 吊销

- 即时生效：所有受保护 API（项目/照片/证据/同步/原图）在 `resolve_device` 处拒绝
- 待决挑战随吊销作废
- 幂等：重复吊销返回同一结果
- 吊销**不删数据**：设备的项目/照片保留（只拒新访问）

### 兼容迁移

SQLite 优先查找 → 找不到回退旧 `devices.jsonl`（只读兼容）。新注册只写 SQLite。旧设备**无需重注册**。

### 挑战响应防重放

- nonce 存 `mvp_auth_challenges`（非内存）——服务重启后未过期挑战仍有效，重启不泄漏也不误杀
- **单次使用**：验证时原子消费（并发双放只成功一个）
- 60s 过期 + 绑定设备（A 的 nonce B 不能用）

## P1 安全配置与网络门禁

### 启动检查器（`security_config.py`）

| 配置组合 | 级别 | 行为 |
|---|---|---|
| `WORKCAM_ENV=production` + `WORKCAM_UPLOAD_EXPERIMENTAL=1` | **FATAL** | **拒绝启动**（exit 1） |
| production 无注册码 | WARN | 无法注册新设备（可用性问题，非安全问题） |
| `WORKCAM_TRUST_PROXY_HEADERS=1` | WARN | 必须确保反代剥离客户端伪造的 XFF |
| production + 无代理信任 | WARN | 如在 TLS 反代后需设置信任 |

### 代理 IP 信任策略

- **默认不信任** `X-Forwarded-For`——客户端可伪造该头绕过回环门禁
- 信任开启时只取**最右侧**（我们的代理追加的）条目，左侧客户端注入的忽略
- 门禁决策只用 `effective_peer_host()`（socket peer 或受信 XFF）

### 回环门禁（沿用 W005-R1，未回退）

- 无鉴权时非回环客户端 503（代码强制）
- `WORKCAM_UPLOAD_AUTH=1` 是远程访问的唯一合法路径

## P2 审计与限流

### 审计事件（`mvp_audit_log`）

- 覆盖：注册/重放/吊销/挑战发放/验证成败/限流命中 + 原图上传/下载/验证/同步（经既有端点补点）
- **脱敏**：日志里 device_token 只存 16 位指纹；`token/private_key/secret/signature` 字段名强制 `[REDACTED]`；detail 超 300 字截断
- **保留策略**：`prune_audit(max_age_days)` 按时间窗清理
- **如实声明**：这是 append-only **应用日志**，不是防篡改审计链（数据库管理员可删改——防篡改需要外部锚定，见风险清单）

### 限流（`mvp_rate_limits`）

- 滑动窗口，**DB 持久化**：重启不重置、多进程共享同一 DB 即一致
- 每设备独立（A 被限不影响 B）
- 默认 60 次/60 秒；超限 429 + 审计事件

## P3 部署（详见 w009-deployment.md）

- 环境变量模板（`tools/deploy/env.template`）
- 启动前配置校验（FATAL 中止 / WARN 警告）
- 健康检查 `/health`；优雅关闭（uvicorn 信号处理）
- 备份：DB `VACUUM INTO` + 原图逐个复验（W007）+ 身份数据（v5 后在同一 DB）
- 恢复演练：恢复后身份/项目/照片/同步状态一致（测试钉死）

## 已知边界与生产差距（如实）

1. **注册码即管理员**——MVP 简化；生产需操作员 RBAC
2. **应用日志非防篡改**——生产需外部锚定或 WORM 存储
3. **无 TLS 终端**——由反向代理承担；代理必须剥离客户端 XFF
4. **SQLite 单写者**——受控内网单实例足够；多实例需 SQLite→PostgreSQL 迁移
5. **速率限制窗口粒度**——对齐秒级窗口，突发整形非令牌桶（对内网联调足够）
