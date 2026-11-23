# WorkCam W008 离线同步协议

版本 v1 · 分支 `dev/windows-mvp-w008`（基于 PR #13 HEAD `c11c279`）
全量 **492 passed / 0 failed**（470 基线 + 22 新增）

## 协议设计（编码前已报告）

### 核心决策

| 决策 | 说明 |
|---|---|
| **不建第二套上传** | 原图字节同步复用 W007 端点（先元数据后字节）；超大文件分块已在 T12 实验层有评估基线 |
| **幂等台账** | `mvp_sync_ops`（operation_id + owner_token 复合主键 + 请求哈希 + 回执 JSON）——服务重启不丢（DB 落库） |
| **游标** | `mvp_sync_events.seq`（AUTOINCREMENT）重启稳定；分页窗口先取高水位再查页——并发插入不漏不重 |
| **乐观并发** | `mvp_projects.revision` 计数器；不匹配返回 409 + 当前版本 + 恢复建议——**绝不 LWW** |
| **信任边界** | 客户端时间/device_id 是数据；证据状态只由既有 verify 端点计算——同步引擎不写可信状态 |

### 版本化接口

```
POST /api/v1/sync/batch       提交离线队列（每项独立回执）
GET  /api/v1/sync/events      增量拉取（游标 + 分页 + 实体过滤）
```

### 批量同步语义

请求体携带 `operations[]`（每项 `{op, operation_id, payload}`）。
**按序执行**——同一批次内"建项目→登记照片"自然满足依赖（照片用项目回执返回的 server_id）。逐项回执（部分成功）：

```json
{ "batch_received": 3,
  "receipts": [
    {"operation_id": "...", "status": "applied",   "result": {...}},
    {"operation_id": "...", "status": "duplicate", "result": {...}},
    {"operation_id": "...", "status": "rejected",  "error": {"code": "..."}} ],
  "id_map": {"local-id": "server-uuid", ...} }
```

### 幂等规则

| 场景 | 行为 |
|---|---|
| 同 op_id + 同内容（同 owner） | **duplicate**——返回存储回执，零重执行 |
| 同 op_id + 不同内容 | **rejected** `op_id_conflict` |
| 同 op_id + 不同 owner | 各自独立执行（台账 owner 隔离——重放被捕获的请求不能碰别人资源） |
| 确定性失败的重试 | 返回存储的 rejection 回执 |
| 服务重启后 | 台账在 DB——回执不变 |

### ID 映射

`id_map`：`local_id → server_id`（项目用 `server_project_id`，照片用 `server_photo_id`）。客户端离线期间只持有 local_id；同步回执后持久化映射。

## 冲突策略（详见 w008-conflict-policy.md）

| 冲突类型 | 冲突码 | HTTP 语义 | 恢复建议 |
|---|---|---|---|
| 乐观并发 | `revision_conflict` | 409 + `current_revision` | re-fetch → 合并 → 带新 expected_revision 重试 |
| 幂等键重用 | `idempotency_conflict` | 409 | 检查 existing_photo_id；同内容可安全忽略 |
| op_id 重用 | `op_id_conflict` | 逐项 rejected | 审核操作 ID 生成器的唯一性 |
| 归档写入 | `project_archived` | 409 | 项目不可变；照片不可加 |
| 跨主体 | `not_found` | 404 | 与不存在不可区分——无预言机 |

## 增量拉取

```
GET /api/v1/sync/events?cursor=<last-seq>&limit=<page-size>&entity_type=<filter>
```

- 游标 = 客户端已见的最大 seq
- **分页窗口**：先取 `MAX(seq)`（高水位）再查 `seq > cursor AND seq <= min(high_water, cursor+limit)`——mid-pull 并发插入落入下一页
- 响应含 `has_more`（高水位 > 窗口右端）
- `entity_type` 过滤：project / photo / evidence_link / original
- **跨主体隔离**：owner_token 过滤——设备 B 拉不到设备 A 的任何事件

## 故障恢复矩阵（测试钉死）

| 场景 | 测试 | 行为 |
|---|---|---|
| 请求超时→重试 | `test_client_duplicate_sends` | duplicate 回执 |
| 上传回执丢失→重试 | `test_upload_success_receipt_lost` | idempotent_replay |
| 服务端重启 | `test_idempotency_ledger_survives_restart` | 台账/游标不变 |
| 部分批次成功 | `test_partial_success_with_per_op_receipts` | 逐项回执 |
| 游标非法 | `test_invalid_cursor_rejected` | 422 |
| 两设备同改 | `test_two_devices_same_project_conflict_is_typed` + `test_optimistic_concurrency_conflict` | typed 409/404 |
| 幂等键不同哈希 | `test_immutable_sha_conflict` | 409 |
| 网络闪断两次（全链重放） | `test_simulated_offline_then_sync_full_chain` | 全部 duplicate，零重复 |

## 迁移 v4

```
mvp_projects.revision   乐观并发计数器（ALTER TABLE ADD，v1-v3 数据保留）
mvp_sync_ops            幂等台账（operation_id+owner_token 复合主键）
mvp_sync_events         变更日志（AUTOINCREMENT seq = 重启稳定游标）
```

幂等/重放验证：fresh DB 直达 v4、重放零操作、v3→v4 保留数据。
