# WorkCam W008 冲突处理策略

版本 v1 · 分支 `dev/windows-mvp-w008`

## 原则

**禁止无条件最后写入覆盖（LWW）**。所有可变字段冲突必须返回机器可读的冲突码、当前版本号和恢复建议。

## 策略矩阵

### 1. 项目名称/描述/地点（可变字段）——乐观并发控制

- 每个项目带 `revision`（整数，初始 1）
- 更新操作必须携带 `expected_revision`
- 不匹配 → `revision_conflict`（409），详情含 `current_revision` + `recovery` 建议
- 恢复路径：客户端 re-fetch → 合并差异 → 用新 revision 重试
- 服务端每次更新 `revision + 1` 并在变更日志记录

```
设备 A：update(pid, rev=1, name="A edit")  → applied, rev → 2
设备 B：update(pid, rev=1, name="B edit")  → rejected (revision_conflict,
                                             current_revision=2)
设备 B：re-fetch → update(pid, rev=2, name="B edit") → applied, rev → 3
```

### 2. 原图 SHA-256 —— 不可变，冲突直接拒绝

- 照片登记时 `original_sha256` 固化
- **没有任何 API 路径可以修改已登记的哈希**（W006/W007 已实现）
- 同一项目 + 同一 `idempotency_key` + **不同哈希** → `idempotency_conflict`（409）
- 同哈希 + 同内容 → 幂等重放（安全忽略）

### 3. 已归档项目 —— 状态冲突

- `project_archived`（409）：项目不可更新、不可加照片
- 归档是单向操作（幂等）
- 归档**不删除**任何照片/证据/原图

### 4. 证据验证状态 —— 只能由服务端计算

- 同步载荷里携带的任何 `verification_status` 字段被**忽略**
- 状态转换的唯一路径：`POST .../evidence-links/{lid}/verify`（W006 冻结协议）
- 同步引擎只**镜像**（READ）状态到变更日志——**不写**

### 5. 跨主体访问 —— 404 等价

- 冲突码统一为 `not_found`——**不泄露资源归属**
- 无法通过冲突响应推断其他主体是否拥有某资源

### 6. 操作 ID 冲突

| 场景 | 冲突码 | 处理 |
|---|---|---|
| 同 op_id + 同内容 + 同 owner | **不冲突**——duplicate 回执 | 客户端安全忽略 |
| 同 op_id + 不同内容 + 同 owner | `op_id_conflict` | 客户端审核 ID 生成 |
| 同 op_id + 不同 owner | **不冲突**——台账 owner 隔离 | 各自独立执行 |

## 不采用 LWW 的理由

工程照片是**证据**。LWW 意味着：设备 A 拍了照片并修改项目名，设备 B 同时离线修改了项目名——恢复后 B 静默覆盖 A 的修改，A 不知道自己的操作丢了。乐观并发让 B **知道**发生了冲突并做出决策。

LWW 只适用于**无冲突价值**的字段。WorkCam 的业务字段（项目名、地点、归档状态）都是**审计相关**的——每个变更都记录在变更日志中。

## 客户端合并建议（Mac 参考）

```swift
// 收到 revision_conflict 后：
1. GET /api/v1/projects/{pid}          // re-fetch, 拿到 revision=N
2. 比较本地变更 vs 服务端当前值           // 显示给用户或自动合并
3. POST /api/v1/sync/batch with
   expected_revision = N                 // 用新 revision 重试
4. 如果再次冲突 → 重复 1-3（活锁上限 3 次后提示用户）
```
