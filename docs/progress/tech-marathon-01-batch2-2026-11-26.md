# TECH-MARATHON-01 · 第二批进度（2026-11-26 晨）

分支 `dev/windows-tm-security`（第一批基线 `0038c81` → 本批 HEAD `83e603c`）
全量回归：**1095 passed, 3 skipped / 0 failed**（9:36）

## 第一阶段：可信证据体系补齐

| 任务 | Commit | 交付 | 测试 |
|---|---|---|---|
| TM02 真实协议跨语言向量 | `65ada6b` | 100 组真实 canonical payload 签名向量；手写 RFC 6979（钉官方 A.2.5 双向量的精确 r/s）；OpenSSL 独立第二栈验证 96/96 全过；hostile 换行注入被 TM01 builder 新规拒绝；Swift 测试套件已产出（CROSS_PLATFORM_PENDING） | 22 |
| TM06 验证器×十维引擎桥接 | `c4200eb` | W018 六层 → TM07 十维映射；registry 支持 TM03 生命周期状态（rotated_out 保数学失授权、revoked 失败、未知状态永不升 active）；包内 chain/checkpoint 约定入 W018 白名单；公钥替换/链截断具名检出 | 8 |
| TM08 来源与防替换边界 | `0418de2`+`41fcde2` | 来源分类模型 + 可检测性三档（DETECTABLE/HEURISTIC/UNDETECTABLE）；EXIF 只作否定信号；翻拍/合成=启发式候选而非判定；威胁模型文档含攻击矩阵 | 11 |

## 第二阶段：离线时间与定位

| 任务 | Commit | 交付 | 测试 |
|---|---|---|---|
| TM09 锚点 V3 跨模块一致性 | `53ceee0` | W024 状态机 × TM10 区间引擎联合断言：v2 锚点双方都拒绝外推、重启双方都拒绝、新鲜窗口口径差异显式声明；certified 永不入 can_claim | 5 |
| TM10 时间不确定性区间引擎 | `91c5631` | earliest/latest/estimated/uncertainty 四元输出；重启→UNKNOWN（宁缺勿假）；墙钟只作异常信号不作输入；锚点过期开放降级；Swift 可移植公式规格 | 11 |
| TM11 GNSS 研究简报 | `53ceee0` | 离线定位来源分类、8 场景采集矩阵（含对照组）、数据 schema、诚实规则（缺席即数据、a-GPS 分列）；采集 DEVICE_PENDING（Mac 执行） | 文档 |
| TM12 定位质量与异常 | `7cddcc7` | 精度分带、陈旧定位检测、瞬移（>1500km/h 隐含速度）、精确声明与运动矛盾、海拔跳变率；手工定位永不评级；欺骗信号=审查提示非证明 | 13 |

## 第三阶段：第一批成果补充验收

`83e603c`：并发轮换恰一成功（单次 nonce 原子消费）、v13 迁移失败整体回滚（无半迁移残留）、断言计数器 CAS 语义、CBOR 资源上限预拒绝（2^30 数组不在内存物化）、检查点落盘重载判定不变——5/5 通过。

**TM07 集成状态（如实区分）**：十维引擎当前为独立工具 + W018 CLI 桥接（TM06），**未接线生产路由**——接入 verify 端点需要协议评审（时间可信模型 v3 提案中列明），本批不虚报"已接入生产候选"。

## 环境事件（如实记录）

1. wheelhouse 缓存被外部清理过一次（tm-security worktree 重建时在线重装并重建缓存）。
2. `docs/evidence` 目录路径在 backend/docs（TM01 schema 所在），与根 docs/evidence-verifier 并存，均为仓库内相对路径，Mac 按文档内路径取用。

## 推送状态

**临时网络阻塞**：早晨批次推送时 GitHub 443 直连失败 + 代理上游断连（出站 000），本地提交全部完好（83e603c 起 10 个提交待推）。已在做周期性重试；网络恢复后 `git push origin dev/windows-tm-security` 一次即推全部。

## 下一批（按队列）

- 第四阶段 TM31-TM36 存储与同步（继承 W007/W008/W014/W025，重点：崩溃恢复状态机 + 故障注入向量，回应 Mac Night 04 的引用先于证据写入问题）
- TM43-TM45 安全与故障测试
- TM52-TM53 Mac 接入与部署
