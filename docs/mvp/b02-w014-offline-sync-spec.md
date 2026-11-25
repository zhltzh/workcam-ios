# B02-W014 离线同步补齐规范（标注 / 问题 / 关联 / 队列 / 冲突 / 删除）

版本 v1 · 分支 `dev/windows-b02-w014-offline-sync` · 基线 `31917e3`
全量 **644 passed / 3 skipped / 0 failed**（632 基线 + 12 B02-W014 专项）

本篇是 W008 协议（`w008-sync-protocol.md`）与 W013-R1（`w013-r1-acceptance.md`）的增量补齐：
拉取侧事件流扩展、批内依赖引用（local ref）、软删除同步、以及 Mac 端离线队列的
Swift Codable 数据结构与联调规范。

## 1. 本轮补齐的缺口（对照 W013-R1 剩余风险）

| # | 缺口 | 补齐 |
|---|---|---|
| 1 | 标注/问题实体不进 `mvp_sync_events`（拉取侧 CHECK 未扩展） | 迁移 v9：事件流 rebuild，entity_type 增加 `annotation`/`issue`，action 增加 `soft_deleted`/`photo_linked`/`photo_unlinked` |
| 2 | 离线队列引用实体只能靠 server id（跨批次两轮网络） | 批内 **local ref**：`"local:<local_id>"` 由服务端在执行前从本批 id_map 解析 |
| 3 | 软删除不广播，第二设备无从得知 | `annotation_delete` 发 `soft_deleted` 事件 |
| 4 | 依赖顺序无 typed 校验 | 未解析/前向引用 → 409 `unresolved_local_ref`（记台账，重放返回存储回执） |

照片删除 / 问题删除**不引入**：原图是证据，不可变（W006/W007 冻结）；问题用状态机
`closed` 终态表达"完结"，不物理删除。这是有意设计，不是缺口。

## 2. 迁移 v9：事件流 CHECK 扩展

SQLite 无法 ALTER CHECK 约束，`mvp_sync_events` 整表重建（与 v3 evidence_links 同法）：

```sql
entity_type IN ('project','photo','evidence_link','original',
                'annotation','issue')
action      IN ('created','updated','archived','bound','status_changed',
                'soft_deleted','photo_linked','photo_unlinked')
```

- 全部旧行**连同 seq 原值**搬入新表；AUTOINCREMENT 计数器随之迁移，客户端已存游标
  在升级后继续有效，seq 只前进。
- 幂等：fresh DB 直达 v9；v8→v9 只执行一次；重放零操作（测试钉死）。

## 3. 事件流：新实体的 action 矩阵

| op | entity_type | action | revision | payload_hint |
|---|---|---|---|---|
| `annotation_create` | annotation | created | 1 | local_id |
| `annotation_update` | annotation | updated | 新 rev | annotation_id |
| `annotation_delete` | annotation | **soft_deleted** | rev+1 | annotation_id |
| `issue_create` | issue | created | 1 | title |
| `issue_update` | issue | updated | 新 rev | "" |
| `issue_transition` | issue | status_changed | 新 rev | `from->to` |
| `issue_photo_link` | issue | photo_linked | 当前 rev | `role:photo_id` |
| `issue_photo_unlink` | issue | photo_unlinked | 当前 rev | `role:photo_id` |

拉取端不变：`GET /api/v1/sync/events?cursor&limit&entity_type`，`entity_type` 过滤新增
`annotation` / `issue`，owner_token 隔离语义照旧（别人的事件一个也拉不到）。

**软删除的客户端语义**：拉到 `soft_deleted` 即把该 annotation 标记为本地已删（不渲染、
不再提交对它的更新；服务端对已删标注的更新返回 409 `annotation_deleted`）。历史保留
（`is_active=0` 行不物理删除），`GET /photos/{id}/annotations?include_inactive=true` 仍可审计。

## 4. 批内 local ref（依赖引用与顺序校验）

离线时客户端只持有自己生成的 local_id。队列里对实体的引用写成：

```json
{"op": "issue_photo_link", "operation_id": "op-7",
 "payload": {"server_issue_id": "local:lis0",
             "server_photo_id": "local:lph3", "role": "before"}}
```

规则（B02-W014 新增，其余语义全部继承 W008）：

1. 以下四个字段接受 `"local:<local_id>"`：`server_project_id` / `server_photo_id` /
   `server_annotation_id` / `server_issue_id`。
2. 解析发生在**台账命中检查之后、executor 执行之前**：同 operation_id 重放走台账
   （存储回执），不会重新解析。
3. 引用只能指向**本批中更早且 applied** 的操作：前向引用、引用失败源、未知 local_id
   一律 409 `unresolved_local_ref`（回执含 `ref_key` / `local_ref` /
   `resolved_local_ids`），按确定性失败记台账——重试该 op 必须换新 operation_id。
4. request hash 按**原始 payload**（含 `local:` 前缀）计算：同队列重放字节相同。
5. expected_revision 仍由客户端明示；引用本批新建实体时，客户端本地知道 rev=1。

### 4.1 单轮全链（离线队列的真实形态）

一个批内可以完整排队整条依赖链，一次网络往返全部落库：

```
project_create(local:lproj)
 → photo_register(server_project_id="local:lproj", local:lph)
 → annotation_create(server_photo_id="local:lph", local:lan)
 → annotation_update(server_annotation_id="local:lan", expected_revision=1)
 → issue_create(server_project_id="local:lproj", local:lis)
 → issue_photo_link(server_issue_id="local:lis", server_photo_id="local:lph",
                     role="before")
 → issue_transition(server_issue_id="local:lis", expected_revision=1,
                     target_status="in_progress")
```

超过 50 op 的长队列按序切成多批；第二批引用第一批的实体时，客户端已从第一批回执的
id_map 拿到 server id，直接写 server id（不再用 local ref，也避免跨批解析歧义）。

## 5. 冲突与故障矩阵（增量行）

| 场景 | 冲突码 | 行为 | 测试 |
|---|---|---|---|
| local ref 无先前 applied 源 | `unresolved_local_ref` 409 | typed 拒绝，记台账 | test_b02_unresolved_local_ref_typed_rejection |
| local ref 前向引用 | `unresolved_local_ref` 409 | 同上 | test_b02_forward_reference_is_rejected |
| 并发同 expected_revision 更新同一 issue | `revision_conflict` 409 | 恰一个 applied，输家 typed 409，rev=2 无双写 | test_b02_concurrent_edits_exactly_one_winner（双线程） |
| 回执丢失重放整队 | duplicate ×N | 台账回放，id_map 稳定 | test_b02_network_interruption_retry_no_loss |
| 重排队修复冲突 | 新 op id + 新 expected_revision | applied；旧 op id 回放返回旧回执 | 同上 |
| B 设备引用 A 的任何 id | `not_found` 404 | 与不存在不可区分（无预言机） | test_b02_cross_user_and_cross_project_isolation |
| A 自己跨项目 link 照片 | `photo_project_mismatch` 400 | typed | 同上 |
| payload 夹带 verification_status/trust | schema 忽略 | 零 evidence_link 写入 | test_b02_new_ops_cannot_smuggle_trusted_status |

## 6. Swift Codable 数据结构（Mac 客户端离线队列）

命名遵循 `swiftui-mapping.md` 既有体系（W008 冻结 op 名 + mono 显示）。以下结构
与服务端 JSON 字段一一对应，`CodingKeys` 均为默认合成（字段名一致），无需自定义。

```swift
// MARK: - Sync operation queue (B02-W014)

/// One queued offline operation. `op` matches the server's frozen
/// W008/W013-R1 discriminators. Local refs use "local:<id>".
struct SyncOperation: Codable, Sendable {
    var op: String                 // "project_create" ... "issue_photo_unlink"
    var operationId: String        // client-generated, unique per op
    var payload: [String: SyncValue]
}

/// A local reference to an entity created earlier in the same batch.
/// Wire format: "local:<localId>".
struct LocalRef: Codable, Sendable, RawRepresentable {
    var rawValue: String           // "local:lan-17"
    var localId: String { String(rawValue.dropFirst("local:".count)) }
    static func make(_ localId: String) -> LocalRef {
        LocalRef(rawValue: "local:\(localId)")
    }
}

/// Per-operation receipt from POST /api/v1/sync/batch.
struct SyncReceipt: Codable, Sendable {
    var operationId: String
    var status: String             // "applied" | "duplicate" | "rejected"
    var result: [String: SyncValue]?
    var error: SyncErrorEnvelope?
}

struct SyncErrorEnvelope: Codable, Sendable {
    var code: String               // "revision_conflict", "unresolved_local_ref", ...
    var message: String
}

struct SyncBatchResponse: Codable, Sendable {
    var batchReceived: Int
    var receipts: [SyncReceipt]
    var idMap: [String: String]    // localId -> serverId (CodingKeys: id_map)
}

// MARK: - Event stream (GET /api/v1/sync/events)

struct SyncEvent: Codable, Sendable {
    var seq: Int32                 // restart-stable cursor
    var entityType: String         // project|photo|evidence_link|original|annotation|issue
    var entityId: String
    var action: String             // created|updated|archived|bound|status_changed|
                                    // soft_deleted|photo_linked|photo_unlinked
    var revision: Int?
    var payloadHint: String?
    var createdAt: String
}

struct SyncEventsPage: Codable, Sendable {
    var items: [SyncEvent]
    var cursor: Int32
    var hasMore: Bool               // CodingKeys: has_more
    var limit: Int
}
```

注意事项：

- JSON 键是 snake_case（`operation_id`、`id_map`、`has_more`、`batch_received`），
  Swift 侧用 `CodingKeys` 映射（上例注释处）或 `.convertFromSnakeCase` 解码 +
  显式 `CodingKeys` 编码；**建议显式 CodingKeys**，避免解码器策略差异。
- `payload` 是异构字典：用 `SyncValue` 枚举（string/number/bool/null 情况）或按 op
  类型建强类型子结构。强类型子结构的字段与 `/openapi.json` 逐一对齐（见下）。
- op 名一律用 W008 冻结名单，UI 显示用 monospaced（sync op 徽章约定）。

### 6.1 强类型 payload（推荐 Mac 实现）

```swift
struct ProjectCreatePayload: Codable {
    var operationId: String
    var localId: String
    var name: String
    var description: String?
    var siteAddress: String?
}

struct AnnotationCreatePayload: Codable {
    var operationId: String
    var localId: String
    var serverPhotoId: String       // "local:..." or server uuid
    var annotationType: String      // arrow|rectangle|circle|freehand|text|number_marker
    var normX1: Double; var normY1: Double
    var normX2: Double?; var normY2: Double?
    var colorHex: String?
    var strokeWidth: Int?
    var textContent: String?
    var markerNumber: Int?
    var zOrder: Int?
}

struct IssueTransitionPayload: Codable {
    var operationId: String
    var serverIssueId: String       // "local:..." or server uuid
    var expectedRevision: Int       // optimistic concurrency - NEVER omit
    var targetStatus: String        // open|in_progress|resolved|closed
}

struct IssuePhotoLinkPayload: Codable {
    var operationId: String
    var serverIssueId: String
    var serverPhotoId: String
    var role: String                // before|progress|after
}
```

（`project_update` / `photo_register` / `annotation_update` / `annotation_delete` /
`issue_create` / `issue_update` / `issue_photo_unlink` 同法，字段与
`/openapi.json` 对齐，此处不逐一展开。）

## 7. Mac 联调规范

1. **本地队列**：SQLite（Core Data 或 GRDB 均可，系统框架优先）持久化
   `SyncOperation` 表：`operation_id` 唯一索引 + `queued_at` + `status`
   (pending/receipted)。**先落库后执行**，杀进程零丢失。
2. **上传顺序**：photo 的原图字节 PUT 独立于批次（W007 端点）；队列里只放
   `photo_register` 元数据。字节上传失败不影响队列回执，重传幂等
   （同 SHA 重放 `idempotent_replay`）。
3. **重试策略**：网络错误 → 指数退避重发**同一批**（operation_id 不变）；
   `rejected` + `revision_conflict` → 拉取当前 revision，用户合并后**换新
   operation_id** 重排；`rejected` + `unresolved_local_ref` → 检查队列依赖顺序
   （引用的 op 是否排在本 op 之前且 applied），修复后换新 operation_id。
4. **id_map 持久化**：每个回执的 `id_map` 写本地映射表；第二批引用第一批实体时直接
   用映射表里的 server id。
5. **游标**：本地存 `SyncEventsPage.cursor`；`has_more=true` 时循环拉取；
   `soft_deleted` → 本地标注标记删除；`status_changed`/`photo_linked` → 刷新问题详情。
6. **诚实性**（AGENTS.md）：`captured_at`/`device_id` 显示为 client-claimed,
   untrusted；annotation/issue severity 均为 human record；UI 不得把 `duplicate`
   回执显示为新成功。
7. **真机验收清单**（Mac 产出，Windows 端不代答）：
   - [ ] 断网建项目+拍照+标注+问题 → 恢复网络一次同步全链
   - [ ] 飞行模式下杀进程重启 → 队列不丢
   - [ ] 双设备同 expected_revision 编辑 → 恰一赢一 409
   - [ ] 拉取 `soft_deleted` 事件 → 标注消失且历史可审计
   - [ ] 原图字节在同步前后 SHA 不变（设备端核对）

## 8. 测试证据（Windows 端，DESKTOP_SIMULATION）

`backend/tests/test_b02_w014.py`，12 条，全绿：

```
test_b02_v9_migration_carries_events_and_seq
test_b02_v9_fresh_db_reaches_v9
test_b02_events_broadcast_annotation_issue_lifecycle
test_b02_intra_batch_local_refs_full_chain_one_round
test_b02_unresolved_local_ref_typed_rejection
test_b02_forward_reference_is_rejected
test_b02_soft_delete_syncs_to_second_device
test_b02_concurrent_edits_exactly_one_winner      (threaded)
test_b02_cross_user_and_cross_project_isolation
test_b02_network_interruption_retry_no_loss
test_b02_new_ops_cannot_smuggle_trusted_status
test_b02_full_day_scenario_within_batch_cap       (50-op day, 31 photos)
```

全部为合成桌面数据；真机结论由 Mac 端按第 7 节清单产出。iOS 真机交互未验证，
对应能力状态为 Not Implemented（客户端侧）。
