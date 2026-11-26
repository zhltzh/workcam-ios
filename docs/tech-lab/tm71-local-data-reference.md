# TM71 · 客户端本地数据层参考（统一模型 / SQLite Schema / 迁移策略）

Swift 模型与协议：`swift/WorkCamCore/Sources/WorkCamSync/DataModels.swift`
（ProjectRecord / PhotoRecord / IssueRecord / UploadSessionRecord +
LocalDataSource / Repository / OfflineFirstRepository + 内存 double）

## 设计决定（与既有纪律对齐）

1. **管道状态永不入库**（TM70）：`PhotoRecord.hasOriginalOnDisk`、
   `isBound` 是事实字段（F2/F3），TRUSTED_SIGNED 等态由事实**推导**，
   数据库不存第二份会漂移的状态。
2. **离线优先**：读全部本地直答；写先落本地再入 Outbox（TM67）；
   原图字节**永远走 TM69 分块上传器**，不入 outbox。
3. **幂等 upsert 语义**与服务器台账一致（同 id 重放安全）。

## SQLite 参考 Schema（本地库 v1）

```sql
PRAGMA journal_mode=WAL;          -- 与服务端同纪律
PRAGMA foreign_keys=ON;

-- 项目（与服务器 wire 字段一致）
CREATE TABLE projects (
    project_id   TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    site_address TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','archived')),
    revision     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX idx_projects_updated ON projects(updated_at DESC);

-- 照片（业务元数据；证据字段只读事实）
CREATE TABLE photos (
    photo_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(project_id),
    original_sha256 TEXT,             -- NULL until bytes hashed
    original_filename TEXT NOT NULL,
    mime_type    TEXT NOT NULL,
    byte_size    INTEGER NOT NULL CHECK (byte_size > 0),
    captured_at  TEXT,                -- client-claimed, untrusted
    category     TEXT NOT NULL DEFAULT '',
    note         TEXT NOT NULL DEFAULT '',
    has_original_on_disk INTEGER NOT NULL DEFAULT 0,   -- F2 fact
    is_bound     INTEGER NOT NULL DEFAULT 0,           -- F3 fact
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_photos_project ON photos(project_id, created_at DESC);
CREATE INDEX idx_photos_digest ON photos(original_sha256)
    WHERE original_sha256 IS NOT NULL;   -- duplicate candidates

CREATE TABLE photo_tags (
    photo_id TEXT NOT NULL REFERENCES photos(photo_id),
    tag      TEXT NOT NULL CHECK (length(tag) BETWEEN 1 AND 64),
    PRIMARY KEY (photo_id, tag)
);
CREATE INDEX idx_photo_tags_tag ON photo_tags(tag);

-- 整改问题（severity/status/remediation_note 为人工记录）
CREATE TABLE issues (
    issue_id     TEXT PRIMARY KEY,
    project_id   TEXT NOT NULL REFERENCES projects(project_id),
    title        TEXT NOT NULL,
    severity     TEXT NOT NULL
                 CHECK (severity IN ('low','medium','high','critical')),
    status       TEXT NOT NULL CHECK (status IN
                 ('open','in_progress','resolved','reopened')),
    remediation_note TEXT NOT NULL DEFAULT '',
    revision     INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL
);
CREATE INDEX idx_issues_project ON issues(project_id, status);

-- 上传会话（TM69 客户端状态；restart-safe 与服务端 v14 同思路）
CREATE TABLE upload_sessions (
    upload_id    TEXT PRIMARY KEY,
    photo_id     TEXT NOT NULL REFERENCES photos(photo_id),
    claimed_sha256 TEXT NOT NULL,
    chunk_size   INTEGER NOT NULL,
    total_chunks INTEGER NOT NULL,
    received_mask TEXT NOT NULL DEFAULT '[]',   -- JSON int array
    status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','completed','cancelled')),
    updated_at   TEXT NOT NULL
);

-- Outbox（TM67；重启恢复 = sending->pending 由引擎处理）
CREATE TABLE sync_outbox (
    operation_id TEXT PRIMARY KEY,
    op          TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    depends_on  TEXT NOT NULL DEFAULT '[]',     -- JSON id array
    state       TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','sending','acknowledged',
                                 'failed','conflict')),
    attempts    INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error_code TEXT,
    last_error_message TEXT
);
CREATE INDEX idx_outbox_state ON sync_outbox(state, next_attempt_at);

-- 缓存台账（TM72：只有衍生图；原图永不入此表）
CREATE TABLE cache_ledger (
    cache_key   TEXT PRIMARY KEY,      -- "<photo_id>:<kind>"
    byte_count  INTEGER NOT NULL,
    last_used   INTEGER NOT NULL,      -- monotonic counter
    file_path   TEXT NOT NULL          -- under Caches/, never Documents/Originals/
);

-- 拍摄暂存（TM70 CaptureTransaction：marker 最后写）
CREATE TABLE capture_staging (
    capture_id  TEXT PRIMARY KEY,
    staged_original_path TEXT NOT NULL,
    staged_manifest_path  TEXT NOT NULL,
    marker_path TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
-- 完整性：marker 文件存在 + 原图哈希匹配 + manifest 可解析
-- 才可晋升为 photos 行（应用侧扫描，见 EvidenceRecovery.swift）

PRAGMA user_version = 1;
```

## 索引设计依据

- `idx_photos_project(project_id, created_at DESC)`：照片网格默认查询。
- `idx_photos_digest`（部分索引）：TM27/TM72 重复候选检测，只扫有
  摘要的行。
- `idx_outbox_state(state, next_attempt_at)`：TM67 drain 的到期批次
  选取。

## 迁移策略（与 v1..v14 服务端链同纪律）

1. 每个版本一个 `PRAGMA user_version` 台阶 + 事务包裹（失败整级回滚，
   TM32/TM45 已验证的服务端纪律在客户端复用）。
2. **只加不改**：列新增带 DEFAULT；约束变化走"建新表→拷贝→换名"
   （服务端 v13 的做法）。
3. 迁移前自动备份 DB 文件（`app-db-v<N>-backup.sqlite3`）；迁移失败
   恢复备份并拒绝启动（fail-fast，绝不在半迁移库上跑）。
4. 旧数据兼容：本地 v1 之前只有 Night 系列测试数据——无真实用户，
   无兼容包袱（如实记录；首版发布前冻结 v1）。

## Mac 接入

1. `LocalDataSource` 用 GRDB/SQLite 实现（Schema 按上表）。
2. `OfflineFirstRepository` 直接实例化（注入 Local + Outbox）。
3. 迁移执行器：启动时跑 `PRAGMA user_version` 台阶（服务端
   `db_admin.py` 语义的 Swift 版）。
