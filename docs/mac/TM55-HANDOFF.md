# WORKCAM · MAC 统一技术交接包（TM55）

基线：`dev/windows-tm-security` @ `2b535a0`（TECH-MARATHON-01 第三批收官）
全量回归：**1159 passed, 3 skipped / 0 failed**
日期：2026-11-26

本文件是 Windows 侧全部可交接能力的**唯一索引**。每项注明实现状态、测试
状态、生产可用性、实验性限制、是否需要 Mac/真机/外部服务、当前风险。
Mac 按此清单逐项取用材料；材料本身在仓库内，路径均已给出。

状态词表：IMPLEMENTED / DESKTOP_VERIFIED / CROSS_PLATFORM_PENDING /
DEVICE_PENDING / POC_VERIFIED / NOT_STARTED。

---

## A. 密码学与可信证据（第一优先队列）

### A1. 证据协议 Schema（TM01）— DESKTOP_VERIFIED
- 材料：`backend/docs/evidence/evidence-schema-v1.json`（机器可读合同）
  + `backend/tools/evidence_schema.py`（独立校验器，Swift 可逐行移植）
- 状态：**已实现并测试**（10 tests）。未知协议版本**永不信任**规则已钉死。
- 生产可用性：校验器为独立工具，**未接线生产路由**（协议 v2 提案待评审）。
- 需要 Mac：是——Swift 侧校验器移植 + 向量执行。
- 风险：无已知风险；canonical payload 字节合同已由 TM02 双栈钉死。

### A2. 真实协议跨语言向量（TM02）— DESKTOP_VERIFIED，Swift 侧 CROSS_PLATFORM_PENDING
- 材料：`test-vectors/crosslang/tm02-protocol-vectors.json`（100 组：
  输入+canonical 文本+SHA256+确定性 DER 签名）
  + `test-vectors/crosslang/TM02ProtocolVectorTests.swift`（Mac 执行的
  XCTest 套件，含 DER→raw 转换）
- 验证：Python cryptography + **OpenSSL 独立双栈 96/96**；RFC 6979 手写
  实现钉官方 A.2.5 向量。
- 生产可用性：向量可直接进回归。
- 需要 Mac：是——执行 Swift 套件并回报结果。
- 风险：Swift DER→r||s 转换代码未经 Mac 验证（套件内自带）。

### A3. 设备密钥生命周期（TM03）— DESKTOP_VERIFIED，生产就绪
- 材料：`/api/v1/devices/rotate/prepare|commit`、`/{token}/suspend|resume`、
  `/{token}/keys`、`/keys/status` 六端点（v13 迁移）
  + `docs/api/openapi.json`（75 paths/88 ops 含安全注解）
- 状态：**已接线生产路由**。轮换需旧钥持有证明（单次 nonce），回滚防护
  （rotated-out 不得再注册），suspended 可逆、revoked 终局。
- 需要 Mac：是——iOS 端 Secure Enclave 轮换流程 + 真机验证。
- 风险：无已知风险；并发轮换恰一成功已测。

### A4. App Attest 服务端验证（TM04 + TM43）— DESKTOP_VERIFIED，真机 DEVICE_PENDING
- 材料：`backend/tools/appattest/`（cbor_min.py 零依赖解析器 +
  app_attest_verify.py + 合成向量生成器）+ `README.md`（**Mac 接入协议**：
  challenge→attestKey→verify→assertion 全流程 + 真机验收清单）
- 状态：验证逻辑全测（17 tests 含环境隔离）；**合成 CA ≠ Apple 材料**。
- 诚实边界（不可跨越）：App Attest 验证通过 ≠ 照片签名密钥来自 Secure
  Enclave——同钥挑战绑定是独立验证步骤，引擎永不自动假设。
- 需要 Mac：是——真实 Apple 根证书 + 真机 attestKey/assertion 交叉验证。
- 风险：未接真实 Apple 根前的所有结论仅对合成向量成立。

### A5. 证据审计链（TM05）— DESKTOP_VERIFIED
- 材料：`backend/tools/evidence_chain/audit_chain.py`（分叉/删除/插入/替换
  检测 + Merkle 树/证明 + 签名检查点）
- **核心结论**：纯本地哈希链**不能证明链尾未截断**（实验证明）；外部签名
  检查点是唯一防线（同批实验证明其有效）。
- 生产可用性：独立工具；检查点锚定需服务端集成（协议演进项）。
- 需要 Mac：否（服务端机制）。
- 风险：检查点频率/存储策略需产品决策（DECISION_REQUIRED）。

### A6. 十维可信引擎（TM07）+ W018 CLI 桥接（TM06）— DESKTOP_VERIFIED
- 材料：`backend/tools/trust_engine/trust_levels.py`（十维独立报告，
  **无总分**，每维 reason/evidence/failure_condition）
  + `tools/evidence-verifier/trust_bridge.py`（六层→十维映射）
  + `tools/evidence-verifier/verify_evidence.py`（独立 CLI：目录/ZIP、
  zip-slip 防护、六层报告、退出码合同）
- 生产可用性：引擎**未接线生产路由**（v3 提案）；CLI 可独立分发。
- 需要 Mac：是——用 Mac 导出的真实证据包跑 CLI 交叉验证（DEVICE_PENDING）。
- 风险：十维接入生产 verify 流程前需协议评审（任务书第 09 节纪律）。

### A7. 来源与防替换边界（TM08）— DESKTOP_VERIFIED（研究+模型）
- 材料：`backend/tools/provenance/source_model.py` +
  `docs/evidence/tm08-provenance-threat-model.md`（攻击矩阵：
  可检测/启发式/不可检测三档）
- 诚实边界：屏幕翻拍/合成=启发式候选**永不判定**；EXIF 只作否定信号。
- 需要 Mac：否。风险：无。

## B. 离线时间与定位（第二优先队列）

### B1. 时间状态机（W024 继承 + TM09 一致性）— DESKTOP_VERIFIED
- 材料：`backend/techlab/timegeo_trust.py`（8 时间态 × 9 定位态状态机）
- TM09 增量：与 TM10 区间引擎**联合一致性测试**（v2 锚点双方都拒绝外推、
  重启双方都拒绝、certified 永不入 can_claim）。
- 生产可用性：未接线（v3 提案 `docs/proposals/` 待评审）。
- 需要 Mac：是——真实锚点收据 + 真机时钟行为。
- 风险：v2 生产锚点缺单调钟映射（v3 提案解决）。

### B2. 时间不确定性区间引擎（TM10）— DESKTOP_VERIFIED
- 材料：`backend/techlab/time_uncertainty.py`：earliest/latest/estimated/
  uncertainty 四元输出；公式（anchor ± rtt/2 + Δt ± drift ± res）+
  **SWIFT_PORTABLE_SPEC**（Swift 逐行移植合同）
- 诚实边界：外推时间**永不是服务器认证的拍摄时间**；重启→UNKNOWN 宁缺
  勿假；墙钟只作异常信号不作输入。
- 需要 Mac：是——真机单调钟漂移参数校准。
- 风险：DeviceClockModel 当前用保守常数（250ppm），真机校准后收窄。

### B3. GNSS 实验矩阵（TM11）— 设计完成，采集 DEVICE_PENDING
- 材料：`docs/tech-lab/tm11-gnss-research-brief.md`（8 场景矩阵含对照
  组、CSV schema、诚实规则：缺席即数据、a-GPS 分列、禁止模拟行）
- 需要 Mac：**是（唯一执行方）**——按简报采集，Windows 用 B02-W016
  分析器管线出统计。
- 风险：无（未开始采集即当前状态）。

### B4. 定位质量与异常（TM12）— DESKTOP_VERIFIED
- 材料：`backend/techlab/location_quality.py`（精度分带、陈旧检测、瞬移
  >1500km/h、精确声明与运动矛盾、海拔跳变率；手工定位永不评级）
- 诚实边界：欺骗信号=审查提示**非证明**；无异常≠真实。
- 需要 Mac：是——真机定位流验证阈值。
- 风险：阈值（30s 陈旧、415m/s）待真机数据校准。

## C. 存储与同步（第三批第一阶段）

### C1. 崩溃恢复状态机（TM32）— DESKTOP_VERIFIED，**Mac Night 04 问题的直接答案**
- 材料：`backend/techlab/recovery_sm.py`（9 派生态 × 5 事实源，**永不存
  副本状态**；Swift 可移植规格）+ `app/services/evidence_recovery.py`
  （只读扫描 + 安全子集应用）
- 关键语义：**引用先于证据写入 = VIOLATION 上报人工**（绝不补行伪造）；
  幂等 rebind（重哈希证明）；孤儿字节永不删；验证永不伪造；并发恢复恰一。
- 11 个崩溃场景全部注入测试（14 tests）。
- 需要 Mac：是——Swift 端 outbox 镜像状态机。
- 风险：无已知。

### C2. 冲突处理（TM34）— DESKTOP_VERIFIED
- 10 种冲突形态零静默覆盖（平行流竞态/幂等键/标签台账漂移/删除终局/
  归档拒写/依赖序/可 rebase 的版本冲突/跨属主 404）。
- 诚实范围：MVP 单属主模型下"多设备"= 同属主双操作流；跨属主隔离另行钉死。
- 需要 Mac：是——双 iOS 实例真实竞态复现。

### C3. 断点续传（TM35）— DESKTOP_VERIFIED，生产就绪
- 材料：`/api/v1/photos/{id}/original-session` 5 端点（v14 迁移）：open/
  chunk/status/cancel/complete
- 保证：分块哈希先验后收、乱序+幂等重发、篡改分块 typed 拒绝、全文件哈希
  证明（不匹配**零入库**）、重启存活（DB+文件即状态）、取消清理。
- 需要 Mac：是——iOS 端分块上传器接入（弱网真机验证）。

### C4. 项目级备份包（TM36）— DESKTOP_VERIFIED
- 材料：`app/services/backup_v2.py`：precheck 先验（摘要+逐对象哈希）、
  暂存导入失败回滚、skip-existing 幂等、原图永不覆盖、v1→v2 迁移。
- 需要 Mac：否。风险：无已知。

## D. 安全与故障（第三批第二阶段）

### D1. 环境隔离（TM43）— DESKTOP_VERIFIED
- dev 链在生产锚下必败（镜像同测）；x5c 结构性无根（锚永不来自客户端）。
- 真实 Apple 根交叉验证 DEVICE_PENDING。

### D2. 解析器模糊（TM44）— DESKTOP_VERIFIED，**3 个真实缺陷已修**
1. schema 文档缺 sequence_number 上限（vs 生产模型）→ 文档+校验器补齐
2. CBOR 2000 层深嵌套栈 DoS → 深度上限 64 + NaN/Inf 拒收
3. PackageRecord 接受穿越路径 → 模型级 `_safe_rel_path`
- 敌意 JSON/CBOR/DER/Base64/ZIP（炸弹+穿越+大小写碰撞）全部 typed 拒绝。

### D3. 故障注入（TM45）— DESKTOP_VERIFIED，**1 个生产缺陷已修**
- `db()` 在锁竞争下级联 "cannot rollback" 掩盖真因 → 分层修复（单层具名
  异常）；锁争用有界不中毒；中断批次前缀持久可续；验证失败半径=一条链接。

## E. API 契约与部署（TM52/TM53 收口）

### E1. OpenAPI 契约 — DESKTOP_VERIFIED
- `docs/api/openapi.json`：**75 paths / 88 ops**（RC2 集成 + TM03 + TM35），
  securitySchemes + 逐端点注解（2 admin / rest deviceToken）+ enrichment
  标记；契约测试 13 项钉死文档=实现。
- `docs/api/b02-w026-mock-data.json`：7 场景 70 次真实调用录制。

### E2. Swift Codable 与联调 — 见 W026 交付（`docs/api/` 联调指南）

### E3. 部署 — DESKTOP_VERIFIED
- `backend/bootstrap.ps1` + `scripts/db_admin.py`（v1..v14 迁移+回滚演练）
  + `start_dev.ps1` + 备份脚本；Dockerfile 未本机构建（无 Docker Desktop，
  如实记录）；GitHub Actions yml 交付未启用。
- **干净克隆验证已过**：clone → bootstrap → 14 migrations → /health →
  live 75 paths 与契约一致（对 `2b535a0` 重跑一次即可复现）。

## F. 真机验收清单（Mac 执行，Windows 永不代答）

| # | 项目 | 材料 | 判据 |
|---|---|---|---|
| 1 | TM02 Swift 向量套件 | A2 的 XCTest 文件 | 全向量 verify 通过 |
| 2 | TM04 真实 App Attest | A4 README 验收清单 | 真 attestKey 过 Apple 根；dev 在 prod 锚下被拒 |
| 3 | 同钥绑定挑战 | A4 README | 照片签名密钥签 WorkCam challenge 成功 |
| 4 | TM10 单调钟校准 | B2 公式 | 真机 drift_ppm 实测 ≤ 声明值 |
| 5 | TM11 GNSS 采集 | B3 简报 | 8 场景 CSV（缺席行为也记录） |
| 6 | TM12 阈值校准 | B4 | 真机定位流过检异常率合理 |
| 7 | TM35 弱网分块上传 | C3 | 中断/续传/重复分块真机复现 |
| 8 | TM32 端侧状态机 | C1 Swift 规格 | 与服务端扫描结论一致 |
| 9 | 真实证据包 CLI 验证 | A6 CLI | Mac 导出包过验证器（六层+十维） |
| 10 | 密钥轮换真机流程 | A3 | Secure Enclave 轮换走通 6 端点 |

## G. 需要产品/用户决策（DECISION_REQUIRED）

1. 十维可信引擎接入生产 verify 流程（协议评审，v3 提案在 `docs/proposals/`）
2. TM05 检查点锚定频率与存储
3. W025 原图删除策略、配额 enforce（当前 report_only）
4. 多租户/商业化（B02-W028 文档，不阻塞 RC2）
5. gh 认证（PR 创建全局 BLOCKED，分支均已推远端）

## H. 已知风险（开放）

- RISK-W021-R1：PUT original 整体内存缓冲（流式替代=TM35 会话，切换待评审）
- TM04/B3/B4 真机校准未做前，保守参数是唯一选择（已如实标注）
- C2 冲突测试在单属主模型语义下；多属主协作是 B02-W048 演进项
