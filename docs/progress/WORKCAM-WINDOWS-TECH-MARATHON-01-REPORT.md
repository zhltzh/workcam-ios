# WORKCAM-WINDOWS-TECH-MARATHON-01 · 统一研发报告

分支：`dev/windows-tm-security` · HEAD：`2b535a0`（已推远端）
全量回归：**1159 passed, 3 skipped / 0 failed**（7:02）
基线链：`31917e3`（RC2 预合并基线）→ `0038c81`（批一夜）→ `fd44371`（批二）→ `2b535a0`（批三）
日期：2026-11-26 · 模式：Windows 单主会话连续研发

---

## A. 总体研发结果

### 55 项任务状态总览

**已完成（DESKTOP_VERIFIED 或继承验收）：TM01-TM12、TM31-TM36、TM43-TM45、TM52、TM53、TM55**（共 22 项）

**部分/继承：TM14**（LiDAR 分析器 B02-W016 交付，分组统计升级待真机数据）、**TM37**（C2PA 研究+PoC B02-W019 交付，隔离未接线）、**TM46**（压测 B02-W020 三阶段 1k/5k/10k 真实百分位）、**TM54**（tm-security 分支即当前候选主干；正式 RC3 集成候选待测量批次）

**未开始（第四/五优先队列，按开发书尚未轮到）：TM13、TM15-TM23、TM24-TM30、TM19 体积算法子项、TM28-TM29、TM38-TM42、TM47-TM51、TM48-TM49 多租户深化**

### 已有成果继承情况
- W001-W013 / B02-W014~W028 / RC2 预合并全部继承，未重做。
- B02 系 15 项在 RC2 分支 `d543e9a` 冻结；TM 批次在其上叠加。

### 实际修复缺陷（本马拉松三批合计 5 个真实修复）
1. TM32：Mac Night 04「引用先于证据写入」→ 恢复状态机 VIOLATION 上报语义
2. TM44-①：evidence-schema 文档缺 sequence_number 上限（与生产模型不一致）
3. TM44-②：CBOR 2000 层深嵌套递归栈 DoS → 深度上限 64 + NaN/Inf 拒收
4. TM44-③：PackageRecord 接受路径穿越 → 模型级 `_safe_rel_path` 校验
5. TM45：`db()` 锁竞争下级联 "cannot rollback - no transaction" 掩盖真因 → 分层异常修复

---

## B. 可信证据成果

| 能力 | 交付 | 测试 |
|---|---|---|
| 证据协议 Schema | 机器可读合同 + 独立校验器；未知版本永不信任 | 10 |
| 跨语言真实协议向量 | 100 组 canonical payload 确定性签名；RFC 6979 手写钉官方 A.2.5；**OpenSSL 独立双栈 96/96**；Swift XCTest 套件（CROSS_PLATFORM_PENDING） | 22 |
| 设备密钥生命周期 | 6 端点接线生产：持有证明轮换、回滚防护、可逆停用、历史审计、注册态与签名数学分离 | 7+5 |
| App Attest 服务端验证 | 零依赖 CBOR + 链/nonce/应用身份/counter 防重放；合成向量（**非 Apple 材料**）；环境隔离钉死 | 12+5 |
| 证据审计链 | 四类篡改检测 + Merkle 证明 + 签名检查点；**链尾截断本地不可检测**实验证明 | 9 |
| 独立验证器 | W018 CLI（六层/退出码/zip-slip）+ TM06 十维桥接 | 8+60 回归 |
| 十维可信引擎 | 无总分、每维三件套、客户端声明永不提升 | 15 |
| 来源边界 | 可检测性三档攻击矩阵；翻拍=启发式永不判定 | 11 |

## C. 时间定位成果

- 时间状态机 8×9（W024 继承）+ TM09 跨模块联合一致性（5 tests）
- **TM10 区间引擎**：earliest/latest/uncertainty；重启→UNKNOWN 宁缺勿假；Swift 公式规格（11 tests）
- TM11 GNSS 矩阵：8 场景设计完成，采集 DEVICE_PENDING（Mac 唯一执行方）
- TM12 定位异常：分带/陈旧/瞬移/海拔/手工永不评级（13 tests）

## D. 工程测量成果

TM14 分析器（B02-W016 继承：三模式分组统计、失败样本保留、合成数据严格标注 DESKTOP_SIMULATION）。TM15-TM23（几何/点云/面积/角度/体积/校准/格式）属第四优先队列**未开始**——如实记录，不虚报。

## E. 安全与可靠性成果

- TM43：App Attest 环境隔离（dev≠prod 锚，双向）
- TM44：解析器模糊全家桶（JSON/CBOR/DER/Base64/ZIP 炸弹+穿越/manifest），3 真实缺陷修复
- TM45：故障注入（DB 锁/中断批次/验证半径/备份哈希翻转/时钟跳跃），1 生产缺陷修复
- TM32：11 个崩溃场景恢复（四不变量：序严格/字节神圣/幂等/无证明不升级）
- TM34：10 种冲突零静默覆盖
- TM35：断点续传生产就绪（5 端点，全文件哈希证明零误收）
- TM36：项目备份包 v2（precheck/回滚/幂等/永不覆盖）

## F. 国际标准与交付

C2PA（B02-W019 继承：研究报告+真 PoC 8 份 JSON 证据，官方栈 MIT/Apache-2.0，建议不进 RC2）。RFC 3161（TM38）、PDF/A、BagIt、验证网页（TM38-TM42）**未开始**（第四优先队列）。

## G. Mac 接入

**统一交接包：`docs/mac/TM55-HANDOFF.md`**（唯一索引：A1-A7 密码学 / B1-B4 时间定位 / C1-C4 存储同步 / D1-D3 安全 / E1-E3 契约部署 / F 十项真机验收清单 / G 产品决策项 / H 开放风险）。固定 SHA：`2b535a0`。

## H. Git 与验收

- 分支 `dev/windows-tm-security` 已推远端（`2b535a0`）；本地 bundle 备份两份
  （`backups/tm-security-fd44371.bundle`、`backups/tm-security-90a3646.bundle`）
- PR：**全局 BLOCKED（gh 未登录）**——无伪造；15 分支 + tm-security 均在远端
- 复现：`git clone --branch dev/windows-tm-security` → `backend/bootstrap.ps1`
  → `python -m pytest -q` → 1159 passed
- 迁移链：v1..v14（v13=密钥历史+设备表重建、v14=分块会话）；失败回滚原子性已测
- 契约：75 paths / 88 ops，文档=实现由 13 项契约测试钉死

## 已知风险与下一批

1. 十维引擎未接线生产路由（协议评审前置，v3 提案就绪）
2. 真机校准四项（App Attest 真根/单调钟/GNSS/定位阈值）DEVICE_PENDING
3. 下一批（第四优先队列）：TM13/TM15-TM23 工程测量算法库 → TM24-TM30 图像
   → TM37-TM42 标准交付 → TM47-TM51 企业储备 → TM54 正式集成候选
