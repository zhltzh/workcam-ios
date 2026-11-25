# 提案：时间锚点协议 v3（离线时间与定位可信证据）

> 状态：**提案（PROPOSAL）**。本文档不修改生产签名协议与现有生产端点行为。
> 依据 AGENTS.md "Task 002 requires explicit review and authorization"，
> 任何接线实施前需显式评审授权。

## 1. 动机（W024 模型实证的 v2 能力边界）

生产 v2 锚点协议（`backend/app/models/time_anchor.py`）签名的字段是：
`anchor_id / key_id / batch_id / server_received_at / challenge /
chain_head_hash / chain_tail_hash / record_count / verification_status`。
设备报告时间（`device_reported_start_time / end_time`）**未签名**。

W024 模型（`backend/techlab/timegeo_trust.py`）证实：

1. v2 锚点**没有单调钟映射**，锚点后离线拍摄的记录无法推算时间，
   只能落在 `DEVICE_REPORTED_ONLY`（测试 `test_v2_anchor_cannot_extrapolate`）。
2. 定位证据不在任何签名覆盖范围内，位置可信等级完全依赖客户端自述。
3. 设备重启后单调钟清零（假设 A2），v2 无 `boot_id`，无法表达"单调链断裂"，
   模型只能退化为不可验证。

## 2. v3 新增字段（全部纳入签名）

### 2.1 锚定请求（TimeAnchorRequest v3）

| 字段 | 类型 | 说明 |
|---|---|---|
| `monotonic_ms_at_anchor` | int ≥ 0 | 客户端发起锚定时的单调钟读数（ms） |
| `boot_id` | str(≤64) | 本次设备启动的随机标识（重启后重新生成） |
| `utc_offset_at_anchor` | str | 锚定时刻时区偏移（本地表示用，绝对语义走 UTC） |
| `record_digests[]` | [str] | 每条记录的 `(record_hash, monotonic_ms, boot_id, utc_offset)` 摘要行 |

### 2.2 锚定回执（TimeAnchorResponse v3）

| 字段 | 说明 |
|---|---|
| 上述全部 v2 已签名字段（不变） | 向后兼容 |
| `monotonic_ms_at_anchor`、`boot_id`（回签） | 服务器确认收到并签名了单调映射的**存在** |
| `protocol_version: "v3"` | 签名数据内，防跨版本混淆（沿用 v2 机制） |

### 2.3 记录清单（随链 hash 覆盖）

每条拍摄记录新增并纳入 `record_hash` 计算：

| 字段 | 说明 |
|---|---|
| `monotonic_ms` | 拍摄时刻单调钟读数 |
| `boot_id` | 拍摄时刻所在启动标识（与锚点不同 ⇒ 单调链断裂） |
| `utc_offset` | 拍摄时刻时区偏移 |
| `location_evidence` | `source / horizontal_accuracy / fix_timestamp / is_simulated`（对齐 T02 `LocationMetadata`） |

## 3. 责任分离（红线，写入协议规范措辞）

1. **服务器只签证据，不签结论**。v3 服务器签名覆盖单调映射与定位证据的
   **存在性**，不评估、不背书推算时间。`EXTRAPOLATED` 估计一律由验证端
   （审核/验真工具）按 W024 状态机计算。服务器回执中**禁止**出现
   "capture time verified" 类字段。
2. 推算时间的对外措辞永远是 "estimate"（存在性上界 + 单调钟差的估计），
   不是服务器已认证的真实拍摄时间。
3. 定位证据是 best-effort GNSS/网络/人工输入的**记录**，不是不可伪造的
   位置证明；`is_simulated` 标志与启发式信号只构成 `SIMULATION_SUSPECT`
   嫌疑，不构成检测保证。
4. `boot_id / monotonic_ms` 由客户端报告，客户端可谎报。服务器签名只证明
   "这些值被提交过"，不证明值诚实。因此锚点后的推算天然受此上限约束，
   状态机 `EXTRAPOLATED_*` 的置信天花板不因 v3 升格。

## 4. 新增端点（提案）

| 端点 | 语义 |
|---|---|
| `POST /time-anchor/v3/anchor` | v3 锚定（新协议版本，与 v2 端点并存） |
| `POST /time-anchor/v3/late-anchor` | 追溯锚定：对已同步批次追加存在性上界，**永不升级**历史时间状态（对齐 `apply_late_anchor` 语义：只追加 `existence_bound`，冲突记录不加） |
| `GET /time-anchor/public-keys` | 沿用 v2 key_id 注册表与轮换机制 |

幂等性、防重放（challenge）、冲突检测（同 batch_id 不同内容拒绝）、
输入校验（控制字符/分隔符注入拒绝）全部沿用 v2/W-003-R2 规则。

## 5. 兼容性

- v2 回执按 v2 规则验证，不受 v3 影响；`protocol_version` 在签名数据内。
- v3 客户端必须能降级：服务器不支持 v3 时按 v2 提交，记录时间状态自动落
  `DEVICE_REPORTED_ONLY`（模型已实现该路径）。
- `verification_status` 语义（DIGEST_ANCHORED 等）不变；v3 锚定请求若含
  全量 `record_digests[]`，可申请 `CHAIN_VERIFIED`。

## 6. 与 W024 模型的对应（验证端实现，Windows 侧已交付）

| v3 字段 | 模型输入 |
|---|---|
| `server_received_at`（签名） | `AnchorEvidence.server_received_at` |
| `monotonic_ms_at_anchor`、`boot_id`（签名） | `AnchorEvidence.monotonic_ms_at_anchor / boot_id` |
| 每条记录 `monotonic_ms / boot_id / utc_offset` | `CaptureTimeEvidence.*` |
| 每条记录 `location_evidence` | `LocationMetadata`（T02） |
| —— | 输出：`TimeTrustState × LocationTrustState`（`timegeo_trust.py`） |

## 7. 实施前置条件

1. 真机验证假设 A1/A2/A3（单调钟抗时间修改、重启清零、模拟定位检测边界）
   —— DEVICE_PENDING，Mac 侧实验清单另行交付。
2. 灵感来自 RFC 3161 的法律责任边界评估：即便 v3，本系统仍不适合作为
   法律级时间证明（沿用 `docs/offline-time-trust.md` 声明）。
3. 显式评审授权（Task 002 流程）。
