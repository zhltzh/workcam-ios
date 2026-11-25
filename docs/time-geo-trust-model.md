# B02-W024 离线时间与定位可信模型（Windows 侧）

状态：模型 + 状态机 + 自动化测试已交付（`backend/techlab/timegeo_trust.py`、
`backend/tests/test_techlab/test_timegeo_trust.py`）。真机验证状态一律为
**DEVICE_PENDING**（见 `docs/mac-experiments/b02-w024-device-pending.md`）。

本文档形式化定义离线拍摄记录的**时间可信状态机**与**定位可信状态机**：
状态、转移条件、与证据字段的绑定、异常判定阈值与降级路径。它是
`docs/offline-time-trust.md`（W-003 时间锚点）与 tech-lab T02/T03 的细化落地，
**不改变**生产签名协议与生产端点行为；协议演进见
`docs/proposals/time-geo-trust-protocol-v3.md`。

---

## 1. 四类时间（严格定义，不得混用）

| 类别 | 值来源 | 语义 | 绝不允许的说法 |
|---|---|---|---|
| **SERVER_ANCHORED（服务器锚定时间）** | 服务器签名回执中的 `server_received_at` | **存在性上界**：该摘要在此时刻之前已存在。对锚点之后创建的记录不构成任何上界 | "服务器认证了拍摄时间" |
| **DEVICE_REPORTED（设备报告时间）** | 设备墙钟（用户可修改） | 未经验证的参考值，受系统时间修改与时区编辑影响 | "可信时间戳" |
| **EXTRAPOLATED（推算时间）** | `锚点墙钟 + (单调钟读数 − 锚点单调钟读数)` | **估计值**，带随离线时长增长的误差上界；仅依赖单调钟连续性 | "服务器已认证的真实拍摄时间" |
| **UNVERIFIABLE（不可验证时间）** | 无可用参照时的归类 | 不支撑任何时间主张（锚点缺失、单调链断裂且未恢复、墙钟异常且无锚点） | —— |

判定优先级：`SERVER_ANCHORED`（记录被签名批次直接覆盖）>
`EXTRAPOLATED`（单调链完好且锚点带单调映射）>
`DEVICE_REPORTED`（仅墙钟可用）>
`UNVERIFIABLE`（墙钟亦被异常污染）。

**措辞红线**（由 `assert_wording_safety()` 在测试中强制）：

1. 推算时间（EXTRAPOLATED）的任何人类可读输出必须含 "estimate" 一词，
   且不得出现 "server-verified / certified / authenticated / true capture time"。
2. 服务器锚定时间必须标注 "not the capture time"（存在性上界，非拍摄时间）。
3. 定位输出不得使用 "unforgeable / tamper-proof / verified position proof"。
   GNSS 是 best-effort 位置，不是不可伪造的位置证明；人工输入坐标是
   "user-entered (input, not a verified position)"，与 GNSS 可信等级不同类。

---

## 2. 设备模型假设（DEVICE_PENDING，真机验证归 Mac 侧）

| # | 假设 | Windows 侧处理 | 验证状态 |
|---|---|---|---|
| A1 | 用户修改系统时间不影响单调钟读数 | 回拨检测依赖此假设：墙钟回退而单调钟前进 ⇒ 判定"系统时间被修改" | **DEVICE_PENDING** |
| A2 | 设备重启后单调钟清零；`boot_id` 随之改变 | `boot_id` 变化或单调钟回退 ⇒ 单调链断裂 ⇒ 推算不可用 | **DEVICE_PENDING** |
| A3 | iOS 无公开 API 可靠检测 mock location | 模拟定位只做**有界启发式**（显式 `is_simulated` 标志、连续完美精度、物理速度矛盾），检出即 `SIMULATION_SUSPECT`，**不声称检测完备** | **DEVICE_PENDING** |

Windows 侧只负责模型、协议与测试工具；真实 iPhone 定位与时钟实验由 Mac
执行（`docs/mac-experiments/b02-w024-device-pending.md`）。

---

## 3. 时间可信状态机

### 3.1 状态

```
NO_EVIDENCE             无任何时间字段
SERVER_ANCHORED         记录摘要被签名锚点批次直接覆盖（existence bound 成立）
EXTRAPOLATED_FRESH      锚点后 ≤24h，单调链完好，估计可用（误差上界小）
EXTRAPOLATED_STALE      24h < 锚点后 ≤72h，估计可用但低置信
EXTRAPOLATED_EXPIRED    锚点后 >72h 或误差上界超限：估计撤回，仅剩未验证的设备时间
DEVICE_REPORTED_ONLY    无可用锚点（含 v2 锚点无单调映射），仅设备墙钟
UNVERIFIABLE_ANOMALY    单调链断裂未恢复 / 墙钟是唯一见证且呈篡改特征
DEGRADED_RECOVERED      异常后重新锚定的记录：存在性上界有效，会话带 degraded 标志
```

### 3.2 转移（事件 → 目标状态；未列出组合为非法转移，直接拒绝）

| 当前状态 | 事件 | 下一状态 | 说明 |
|---|---|---|---|
| NO_EVIDENCE | ANCHOR_VERIFIED | SERVER_ANCHORED | 签名验证通过的锚点覆盖 |
| NO_EVIDENCE | CAPTURE | DEVICE_REPORTED_ONLY | 无锚点时的离线拍摄 |
| SERVER_ANCHORED | CAPTURE | EXTRAPOLATED_FRESH | 锚点后继续拍摄（单调链完好） |
| EXTRAPOLATED_FRESH | CAPTURE | EXTRAPOLATED_FRESH | 继续推算 |
| EXTRAPOLATED_FRESH | WINDOW_ELAPSED | EXTRAPOLATED_STALE | 距锚点超 24h |
| EXTRAPOLATED_STALE | CAPTURE | EXTRAPOLATED_STALE | 继续推算（低置信） |
| EXTRAPOLATED_STALE | WINDOW_ELAPSED | EXTRAPOLATED_EXPIRED | 距锚点超 72h |
| EXTRAPOLATED_{FRESH,STALE} | DRIFT_EXCEEDED | EXTRAPOLATED_EXPIRED | 误差上界 >15s，估计撤回 |
| EXTRAPOLATED_EXPIRED | CAPTURE | EXTRAPOLATED_EXPIRED | 过期后只能续存设备时间参考 |
| EXTRAPOLATED_EXPIRED | RE_ANCHOR / ANCHOR_VERIFIED | SERVER_ANCHORED | 重新联网即恢复锚定 |
| EXTRAPOLATED_{FRESH,STALE} | WALL_ROLLBACK / FORWARD_JUMP / MANUAL_TZ_CHANGE | 自环（保持） | 单调链推算不受墙钟篡改影响；墙钟被标记失格，异常保留 |
| EXTRAPOLATED_{FRESH,STALE} | TIMEZONE_SHIFT | 自环（保持） | UTC 语义连续的时区切换：仅 INFO |
| EXTRAPOLATED_{FRESH,STALE} / SERVER_ANCHORED / DEGRADED_RECOVERED | MONOTONIC_RESET | UNVERIFIABLE_ANOMALY | 重启清零单调钟：锚点映射失效 |
| DEVICE_REPORTED_ONLY | CAPTURE | DEVICE_REPORTED_ONLY | 持续无锚点 |
| DEVICE_REPORTED_ONLY | WALL_ROLLBACK / FORWARD_JUMP / MANUAL_TZ_CHANGE | UNVERIFIABLE_ANOMALY | 墙钟是唯一见证且被污染 ⇒ 不可验证 |
| DEVICE_REPORTED_ONLY | MONOTONIC_RESET / TIMEZONE_SHIFT | DEVICE_REPORTED_ONLY | 单调钟本就未用于推算 |
| UNVERIFIABLE_ANOMALY | RE_ANCHOR | DEGRADED_RECOVERED | 恢复锚定但会话留痕 |
| UNVERIFIABLE_ANOMALY | ANCHOR_VERIFIED | SERVER_ANCHORED | （新锚点直接覆盖新批次） |
| DEGRADED_RECOVERED | CAPTURE | EXTRAPOLATED_FRESH | 新链正常推算，degraded 标志随会话传播 |
| DEGRADED_RECOVERED | MONOTONIC_RESET | UNVERIFIABLE_ANOMALY | 再次断裂 |
| {SERVER_ANCHORED, EXTRAPOLATED_*, DEGRADED_RECOVERED} | SEQUENCE_CONFLICT | UNVERIFIABLE_ANOMALY | 序列顺序与设备时间顺序矛盾 |
| SERVER_ANCHORED | ANCHOR_INVALID | DEVICE_REPORTED_ONLY | 锚点签名验证失败即降级 |

**证据绑定**（每状态必须持有的证据字段，评估器 `assess_time_state` 只认证据）：

- `SERVER_ANCHORED` / `DEGRADED_RECOVERED`：`AnchorEvidence.signature_valid=True`
  且 `CaptureTimeEvidence.covered_by_anchor == anchor_id`；
  `existence_bound = server_received_at`。
- `EXTRAPOLATED_*`：`AnchorEvidence.monotonic_ms_at_anchor`、`boot_id` 匹配、
  `CaptureTimeEvidence.monotonic_ms ≥ 锚点读数`。产出
  `estimate = server_received_at + Δmono`，`error_bound = 5s + 0.18s/h × 离线小时数`。
- `DEVICE_REPORTED_ONLY`：仅 `device_wall_time`；锚点缺失或 v2 锚点
  （无单调映射，生产现状即此）。
- `UNVERIFIABLE_ANOMALY`：异常证据（`monotonic_reset` / `wall_rollback` /
  `forward_jump` / `manual_tz_change` / `sequence_conflict` / `anchor_conflict`）
  且无可恢复锚点。

### 3.3 异常判定阈值（模块常量，测试钉死）

| 异常 | 判定阈值 | 降级路径 |
|---|---|---|
| 墙钟回拨（wall_rollback） | 墙钟回退 > **2.0s**（`ROLLBACK_TOLERANCE_S`），且单调钟前进 | 有锚点：状态保持，墙钟失格（`device_time_disqualified`），推算保留；无锚点：→ UNVERIFIABLE_ANOMALY |
| 前向跳变（forward_jump） | 墙钟前跳 > **6h**（`MAX_FORWARD_JUMP_S`），单调钟差远小于该值 | 同上 |
| 单调钟清零/重启（monotonic_reset） | 单调钟回退 > 2s，或 `boot_id` 变化 | → UNVERIFIABLE_ANOMALY；重新锚定后经 RE_ANCHOR → DEGRADED_RECOVERED |
| 时区切换（timezone_shift） | `utc_offset` 变化但 UTC 值与单调钟差一致（隐含偏移 ≈0） | INFO，不降级（飞行/自动切换） |
| 手动改时区（manual_tz_change） | `utc_offset` 变化且 UTC 值随偏移整体平移（隐含偏移 >2s） | 有锚点：墙钟失格、推算保留；无锚点：→ UNVERIFIABLE_ANOMALY |
| 锚点过期（window_elapsed） | 距锚点 > **24h** 进入 STALE；> **72h** 进入 EXPIRED | STALE：低置信估计；EXPIRED：估计撤回，仅未验证设备时间参考 |
| 漂移超限（drift_exceeded） | 误差上界 = 5s + 0.18s/h（50ppm 保守漂移）> **15s**（`MAX_EXTRAPOLATION_ERROR_S`） | → EXTRAPOLATED_EXPIRED（估计撤回） |
| 锚点冲突（anchor_conflict） | 被锚点覆盖的记录声称设备时间 > `server_received_at` | 异常记录；存在性上界保留，设备时间失格 |
| 序列冲突（sequence_conflict） | 按 `sequence` 排序后设备时间非单调 | → UNVERIFIABLE_ANOMALY |
| 锚点签名无效 | `signature_valid=False` | 锚点整体不可用 → DEVICE_REPORTED_ONLY |

### 3.4 离线记录恢复后的追溯规则（`apply_late_anchor`）

设备长期离线后重新同步，历史记录的可信状态**只追加、不回溯升级**：

1. 迟到的锚点**从不**把历史记录的时间状态升级
   （EXTRAPOLATED 不会变 SERVER_ANCHORED，UNVERIFIABLE_ANOMALY 不会变正常态）。
2. 它只对满足条件的记录**追加存在性上界**
   （`existence_bound = server_received_at`，条件：设备时间不晚于锚点时间）。
3. 声称时间晚于 `server_received_at` 的记录记 `anchor_conflict`，不追加上界。
4. 存在性上界的语义永远是"摘要在该时刻之前已存在"，与拍摄时间无关。

---

## 4. 定位可信状态机

### 4.1 状态

```
NO_LOCATION        无定位（source=none 或坐标缺失）
GNSS_GRADE_A/B/C/D GNSS fix，按水平精度半径分级（≤5m / ≤15m / ≤50m / >50m，沿用 T02 阈值）
NETWORK_DERIVED    Wi-Fi/基站定位，质量封顶 C
MANUAL_INPUT       用户手选/手输坐标：输入类参考，与 GNSS 可信等级不同，永不作位置主张
STALE_FIX          fix 时刻距拍摄时刻 > 300s：位置是"fix 时刻的位置"，不是拍摄位置
SIMULATION_SUSPECT 模拟定位嫌疑：有界启发式触发；非位置主张，非检测保证
```

### 4.2 异常判定阈值与降级路径

| 信号 | 阈值 | 路径 |
|---|---|---|
| fix 过期 | fix 距拍摄 > **300s**（`STALE_FIX_S`） | → STALE_FIX（不作位置主张） |
| 精度劣化 | 水平精度 >50m | GNSS_GRADE_D（仍是位置主张，但仅粗略） |
| 显式模拟标志 | `is_simulated=True`（Xcode Simulate Location 等） | → SIMULATION_SUSPECT |
| 连续完美精度 | 连续 ≥**3** 个 fix 精度 ≤**1m** | → SIMULATION_SUSPECT（真实 GNSS 极少如此稳定） |
| 物理速度矛盾 | 相邻 fix 隐含速度 > **85 m/s**（≈306 km/h） | → SIMULATION_SUSPECT |
| SIMULATION_SUSPECT | 新 GNSS fix 到达 | **锁存**（latched），需人工复核，不自动清除 |

**A3 边界声明**：`SIMULATION_SUSPECT` 是启发式嫌疑状态。iOS 未提供公开的
mock-location 检测 API，本模型不声称能可靠检出模拟定位；三种信号只是
"值得怀疑"的充分条件，不是"被模拟"的充要条件。真机可检出率实验 →
DEVICE_PENDING。

### 4.3 与 T02 的关系

`GNSS_GRADE_*` 分级、`NETWORK_DERIVED` 封顶 C、`MANUAL_INPUT` 语义全部复用
`backend/techlab/location_meta.py`（T02）；W024 新增动态状态
（`STALE_FIX`、`SIMULATION_SUSPECT`）与拍摄时刻绑定，并对全部状态输出
措辞合规的 `location_label()`。

---

## 5. 时间状态 × 定位状态组合

每条记录输出 `TimeGeoTrustReport`：

- `time.state`（8 态）与 `location.state`（9 态）独立评估、独立标注；
  组合共 72 种，`assert_wording_safety` 对全部组合采样通过（见测试
  `test_wording_safety_holds_for_all_state_pairs`）。
- `can_claim` / `cannot_claim` 列出该状态下允许与禁止的主张措辞；
  上报文案一律走 `time_label()` / `location_label()`。

---

## 6. 测试与验证

- 状态机与全部阈值由 `backend/tests/test_techlab/test_timegeo_trust.py`
  （50 tests）用**模拟时钟/模拟定位注入**验证：锚点、回拨、重启（boot 切换）、
  时区两种路径、窗口过期三条路、锚点冲突、追溯规则、定位全状态、措辞红线
  72 组合。
- 本模块未接线任何生产路由；生产协议不变。协议演进提案见
  `docs/proposals/time-geo-trust-protocol-v3.md`。
- 真机（iPhone）时钟回拨/重启/长离线/GNSS 精度实验：**DEVICE_PENDING**，
  清单见 `docs/mac-experiments/b02-w024-device-pending.md`。
