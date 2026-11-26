# M03-A06 · Night 05 数据合同对拍（Windows 后端 ↔ Mac ed7dd7b）

对照源：`origin/codex/workcam-mac-night-05` @ `ed7dd7b`
（`Core/Security/Evidence.swift`、`Core/Storage/CaptureStore.swift`）
对照目标：RC2+ 后端（v1..v14 schema + sync ops）与 WorkCamCore Swift 包。
**只读对拍，不改 Mac 生产代码。** 兼容测试见
`backend/tests/test_techlab/test_m03_a06_night05_contract.py`。

## 逐项对照结论

### 1. EvidenceManifest ↔ 服务器 v1.1 协议 — **一致** ✅
| Night 05 | 后端 canonical_payload | 判定 |
|---|---|---|
| `version = "1.1"`，域分隔 `WorkCam-Sig-v1.1` | 同 | 一致 |
| 字段序 version/record_id/sequence_number/previous_record_hash/photo_hash/capture_timestamp/device_reported_time/device_id/location=none | 同（TM01 合同） | 一致 |
| 换行拒绝 + 64-hex photo_hash + seq 0..1_000_000 | 同（TM44 补齐了文档上限） | 一致 |
| location 固定 "none"（当前版无定位载荷） | 后端支持完整定位，当前快照等价 | 兼容 |
| **签名用 Secure Enclave**（`SecureEnclave.P256.Signing.PrivateKey`） | 后端只验 ECDSA-P256 DER——**SE 的 DER 输出可直接验** | 兼容（真机待验） |

### 2. WorkPhoto ↔ 服务器照片模型 — **需映射** ⚠
- Night 05：文件 URL 身份，`id = 文件名去扩展名`。
- 后端：UUID `photo_id` + `idempotency_key`。
- **映射**：Night 05 的文件名 ID 作 `idempotency_key`（本地→服务端登记后
  记 `server_photo_id` 映射，即 W014 已实现的 local_id→server_id 台账）。
- `capturedAt: Date` → `captured_at` ISO8601（客户端声明，不可信字段，
  双方语义一致）。

### 3. PhotoAnnotation ↔ 服务器标注 — **形状映射** ⚠
- Night 05：`x/y Double + kind? + points? + color? + lineWidth?`，
  注解存 JSON 侧文件（annotations.json）。
- 后端：`norm_x1/norm_y1/norm_x2/norm_y2 + annotation_type +
  color_hex + stroke_width + text_content + z_order`。
- **映射**（兼容测试钉死）：`x/y → norm_x1/norm_y1`；`kind →
  annotation_type`；`points → (norm_x2/norm_y2)`（两点型）或折线表
  （freehand 扩展位）；`color → color_hex`；`lineWidth → stroke_width`。
- 双方坐标都是**归一化 0..1**（与 TM63 相同合同）。

### 4. InspectionIssue ↔ 服务器问题 — **状态词汇需补一个** ⚠
- Night 05：`Status: open / in_progress / resolved / closed`（线性链
  open→…→closed，`next` 单步推进）。
- 后端 `IssueTransitionOp.target_status` 接受
  `^(open|in_progress|resolved|closed)$` — **词汇已含 closed** ✅。
- **差异**：Night 05 的线性链禁止跨步（closed 无 next）；后端
  transition 只按 expected_revision CAS，不限制单步——兼容（后端更宽）。
- `Severity low/medium/high/critical` 与后端 v9 CHECK **一致** ✅。
- `history: [Change]`（timestamp/status/description/severity/photos）→
  后端状态历史靠 `mvp_issues.revision` + sync 事件流；**对齐方案**：
  Change 数组序列化为备注侧的备注事件（v12 remediation_note 或
  issue_update 的 description），或等产品评审决定服务端历史表（DECISION）。
- `photos: [String: String]`（photoID→role）→ 见下条。

### 5. 照片角色（Role: before/progress/after）— **服务器缺位** ❌→P1
- Night 05 用 `photos: [String: String]` 挂角色；后端 v9 有
  issue_photo_link 但**无 role 列**。
- **修复**（本次 A06 落地）：W014 已有 `photo_linked` 事件；本对拍
  在兼容测试中用 `mvp_issue_links` + 备注承载 role 直到正式协议评审
  （DECISION_REQUIRED：加 `role TEXT CHECK(...)` 列属生产协议变更）。
- `ProjectEvidenceLink { projectID: UUID, timestamp: Int64 }` →
  后端 evidence_links 的 project 归属经 photo→project 联结，语义等价。

### 6. DeliveryZIP ↔ ZIP32/128MiB — **正是 M03-D05 的对象** 
- Night 05：手写 ZIP32 stored（无压缩），`bytes.count + data.count
  ≤ 128MiB` 硬预算，UInt16 条目上限。
- 结论：合同已确认这是**真实生产限制**（非猜测）——D05 流式 ZIP 的
  需求成立；在 D05 交付前，后端 delivery-package 端点继续作为 ZIP 生成
  方（无 128MiB 限制），Night 05 端只需回传 manifest 数据。

### 7. PDF 报告数据
- Night 05 无本地 PDF 生成（经后端 delivery-package 拿 PDF）→
  D06 的六模板即其后端对应物，数据合同 = report snapshot_json。

## 落地物

1. 兼容测试（下）：Night 05 的状态链/角色词汇/标注映射/签名形状
   全部以**真实服务器调用**验证。
2. DECISION_REQUIRED 清单更新：issue photo role 列、服务端
   issue history 表、closed 状态的 transition 白名单保持。

## Swift 侧对齐项（WorkCamCore）

- `IssueRecord.Status`（TM71）已含 closed ✅；补 `photoRoles` 字段
  到 B 队列 M03-B05 实现中。
- `PhotoIndexEntry.tags` 已有——B05 用 `role:before|progress|after`
  语义承载在 tags + 专用 role 集合。
