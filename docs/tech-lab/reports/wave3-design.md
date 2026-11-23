# Wave 3 技术设计与接口约定（T07 / T08 / T12 / T14）

状态：RESEARCHED → POC_READY（协议模型 + 自动化测试，25 项）
日期：2026-11-20 · 分支：`dev/windows-tech-lab-wave2`（PR #10）

四项均遵守 R2 红线：**只做不依赖真机硬件即可验证的协议层**；
Mac 侧实现按本约定对齐，字段与语义已在 Windows 侧用测试钉死。

---

## T07 工程图纸与照片关联（`techlab/drawing_pins.py`，7 测试）

### 协议

| 对象 | 关键字段 | 语义 |
|---|---|---|
| `DrawingSheet` | `pdf_content_hash`（整份图纸 sha256）、`rev`、`geo_origin`（可选 WGS84 原点） | 图纸修订以内容哈希定版；修订变化=新对象 |
| 校准 | ≥3 个 `CalibrationPair(sheet 点, world 点)` → 精确仿射解 | 残差超 `max_residual_m`（默认 0.05 m）**拒绝出结果**；共线点直接拒绝 |
| `PhotoPin` | `pin_id, photo_id, sheet_rev, sheet_x/y, placed_from` | `sheet_rev` 必须等于图纸哈希前 12 位，防跨修订串位 |
| `PinVerification` | `world_xy, corroborated, detail` | **placement 与 verification 永不混淆** |

### 诚实性规则（Mac 渲染层必须遵守）

1. 放置来源 `placed_from` 默认 `manual_drop`——钉子位置是用户行为，不是测量
2. 佐证（corroboration）三要素齐全才可能为真：`geo_origin` 已设 + 照片是
   **GPS 级** T02 定位 + 距钉点 ≤ 半径（默认 25 m）；manual/network
   永不佐证，且 `detail` 必须写明原因
3. 校准残差必须随导出展示；3 点拟合不得宣称测绘级

### Mac 待办（真机后）

- PDF 上取 sheet 坐标的取点 UI（PDFKit）
- 修订迁移：v1 协议已定义 `unverified_after_revision` 状态；文本锚点
  自动迁移是 v2 研究项，**不承诺**

---

## T08 AR 测量与照片标注持久化（`techlab/ar_persistence.py`，9 测试）

### 核心不变量

**第三方可重算**：`project_world_to_pixel(record, point)` 仅依赖持久化
字段（内参 + 位姿 + 世界点），Windows 重算结果与 Mac 持久化的
`(persisted_u, persisted_v)` 偏差 > 0.5px 即判 record 损坏——
`verify_record_projection()` 是跨端一致性 oracle。

### 协议要点

| 规则 | 实现 |
|---|---|
| 内参必须**像素单位** | `fx/fy < 300` 判为 ARKit 原始 mm 值，拒绝序列化 |
| 点在相机后方 | 拒绝（`BEHIND the camera`）而非投影出负坐标 |
| 跟踪降级可见 | `Measurement3D.tracking` 枚举；`degrade_report()` 把 limited/not_available 点列入导出块，永不静默 |
| 跨会话合并 | **禁止**（ARKit 世界原点按会话不同）；`merge_cross_session` 是产品层唯一入口，异 session 抛错 |
| 重开照片复现标注 | 无 AR 会话，纯投影计算——协议刻意设计为"几何即数据" |

### Mac 待办（真机后）

- ARKit `simdfloat4x4` → 四元数位姿的序列化对齐（ARKit 是列主序
  + 逆序习惯，Mac 落字段前先跑 Windows 的 identity-pose 测试）
- `sceneDepth` 置信度字段 v2 研究项

---

## T12 弱网断点续传与幂等同步（`techlab/upload_protocol.py`，6 测试）

### 协议（服务端模型即契约；FastAPI 端点 Wave 3+ 落地时必须复现）

```
POST /upload/batch        幂等；同 batch_id 异内容 = 冲突拒绝
POST /upload/chunk/<b>/<i>  乱序可收；重复 = 幂等 ack；哈希不符 = NACK
                            且不占槽位（重发相同字节是唯一修复路径）
GET  /upload/status/<b>   返回 missing 索引（客户端据此续传）
POST /upload/complete/<b>  全部到位后重组；whole-batch sha256 独立校验
                            （逐块通过 ≠ 整体通过，两者都过才算完）
```

### 已钉死的行为（测试即规范）

- 乱序 + 断点：收 2/0 → 掉线 → status 返回 [1,3] → 逆序续传 → complete
- 腐败块 NACK 后槽位可复用（不留下"假在位"状态）
- 攻击面：把合法块换位 + 伪造 plan（逐块全过）→ whole-batch 校验
  拦下（`test_whole_batch_hash_independent_check`，高熵数据）
- batch_id 重用携带不同内容 → 显式冲突，永不覆盖

### Mac 待办

- 客户端重试/退避策略（协议不规定节奏，只规定语义）
- 与 T13 证据包的字节流对接（上传对象=包文件，total_sha256=包哈希）

---

## T14 C2PA 兼容（`techlab/c2pa_bridge.py`，4 测试）

### 研究结论（如实）

1. **C2PA 给的是行业标准容器**：断言（拍摄时间/GPS/设备）可被第三方
   工具（Verify、Firefox、Truepic）读取——采用价值大。
2. **C2PA 不给现场真实性**：签名证明断言完整性 + 签署者身份，签署者
   可以签署谎言——与 WorkCam 自己签名同一边界，文档与 UI 必须同样措辞。
3. **本仓库无法完成真签名**：内容凭证需要 X.509 证书链与外部账户注册，
   属任务红线外（外部账号依赖），如实记为未做。

### 已实现（离线可验证的桥接层）

- `C2PA_ASSERTION_ACTIONS`：WorkCam 清单字段 → C2PA 标准断言名
  （`stds.exif.dateTimeOriginal`、`stds.device.deviceID` 等）映射固化
- 跨系统不变量：桥接断言的 `c2pa.hash.data` **必须等于** WorkCam
  `photo_hash`，不一致判桥接损坏（测试含篡改检出）
- 无哈希的桥接直接拒绝（无意义）

### Mac 后续

- c2pa-swift/c2pa-rs 真实 SDK 集成 + JUMBF 嵌入（断言层与本桥接同构）
- 证书注册是产品阶段决策，不属技术 PoC

---

## 验证

```
pytest tests/test_techlab/test_wave3_pocs.py   -> 25 passed
```

四模块零真机依赖：全部为协议数学/状态机，Windows 侧即为最终验证环境；
真机相关的只有渲染/采集 UI，已逐项列于"Mac 待办"。
