# WorkCam W006 数据模型

版本：v1（`PRAGMA user_version = 1`，`app/db.py` 管理，幂等可重放）

```
mvp_projects                          mvp_photos
─────────────────────────────        ─────────────────────────────────────
project_id   TEXT PK                 photo_id          TEXT PK
owner_token  TEXT NOT NULL  ──────▶  project_id         TEXT NOT NULL → projects
name         TEXT NOT NULL           idempotency_key    TEXT NOT NULL
description  TEXT ''                 original_sha256    TEXT NOT NULL (immutable)
site_address TEXT ''                 original_filename  TEXT NOT NULL
status       'active'|'archived'      mime_type          TEXT NOT NULL
created_at   TEXT (UTC ISO)          byte_size          INTEGER >0
updated_at   TEXT (UTC ISO)          captured_at        TEXT (client-claimed)
archived_at  TEXT nullable           received_at        TEXT (server UTC)
                                      device_id          TEXT (client-claimed)
索引: (owner_token, created_at DESC)  evidence_id        TEXT nullable
                                      created_at         TEXT (UTC ISO)
                                     UNIQUE (project_id, idempotency_key)
                                     索引: (project_id, created_at DESC)
                                          (original_sha256)

            mvp_evidence_links
            ─────────────────────────────────────────────
            evidence_link_id    TEXT PK
            photo_id            TEXT NOT NULL → photos
            evidence_reference  TEXT NOT NULL
            evidence_type       'signature_manifest'|'time_anchor'
            original_sha256     TEXT NOT NULL   ← 链接时刻照片哈希绑定
            verification_status 'unverified'|'cryptographically_valid'
                               |'verification_failed'|'server_anchored'
            failure_reason      TEXT nullable    ← 失败审计
            verified_at         TEXT nullable
            created_at          TEXT (UTC ISO)
            UNIQUE (photo_id, evidence_reference)
            索引: (photo_id, created_at DESC)
```

## 设计约束

1. **外键**：每连接 `PRAGMA foreign_keys=ON`（不能依赖进程级默认）。
2. **时间**：全部 UTC ISO-8601（`app.db.utc_now()`）。
3. **无物理删除**：归档是状态迁移，照片/证据永不随项目消失。
4. **归属**：`owner_token` 在创建时固化；照片归属经所属项目传导判定。
5. **不可变**：`original_sha256` 无修改路径（照片无 PATCH 端点 + 幂等键防重写）。
6. **迁移**：`migrate()` 按 `PRAGMA user_version` 顺序应用，空库可初始化，
   重放零操作；单条失败整级回滚。

## 备份/恢复

- 备份：`VACUUM INTO`（SQLite 一致性在线备份，绝不复制活动库文件）
- 验证：`integrity_check` + `foreign_key_check` + 行数统计
- 恢复：仅允许恢复到**空目标路径**（拒绝覆盖活动库）；恢复后自动复验
