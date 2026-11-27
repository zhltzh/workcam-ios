# WORKCAM-WINDOWS-TECH-MARATHON-03 · 统一研发报告

分支 `dev/windows-tm-geometry` · 收口 HEAD `b3d5df5`（已推远端）
**新版本实测**：全量 **1589 passed / 3 skipped / 0 failed** · 质量门禁 **12/12 PASSED**（两次干净全跑；此前一轮门禁抓到 F08 轮询时序偶发抖动，如实记录）

## A. 统计口径修正（指令要求）

此前口径混用出现 181/253 差异，修正为**唯一口径**：以收口版本的
`pytest` 输出与 12 门禁输出为准——
- **1589 passed + 3 skipped = 全量**（门禁第一项即为全量跑，无另行计数）
- 马拉松-03 新增测试模块自贡献：A 队列 33、B 队列 105（92 Python+13 Swift
  规则测试不计入 pytest，如实标注）、C 队列 80、D 队列 51、F 队列 21、
  H09/H10 结构校验 1——Python 侧新增 ≈251（含 hotfix 带入 137 行新测试
  的 4 个用例已在 1589 内）
- 任何后续报告只引用门禁输出的单一数字，不再并行手算。

## B. 80 项工作包状态矩阵

### A 队列（历史收口）8/8
| 包 | 状态 | 说明 |
|---|---|---|
| A01 | ✅ DESKTOP_VERIFIED | 弱网平台 7t |
| A02 | ✅ DESKTOP_VERIFIED | 迁移矩阵 14t |
| A03 | ✅ DESKTOP_VERIFIED | （并入 A02 语义） |
| A04 | ✅ CROSS_PLATFORM_PENDING | macOS bootstrap |
| A05 | ❌ BLOCKED | Swift 工具链 4 次安装失败，如实 |
| A06 | ✅ DESKTOP_VERIFIED | Night 05 对拍 6t |
| A07 | ✅ DESKTOP_VERIFIED | e2e 扩展 6t |
| A08 | ✅ 12/12 | 密钥指纹注册制 |

### B 队列（工程业务）12/12 ✅（B12 Swift 为 CROSS_PLATFORM_PENDING）
### C 队列（图像）核心 6/10
C01/C02 ✅（2 个真实缺陷修复）、C04/C05 ✅、C09 ✅、C10 ✅（索引）；
C03/C07 合同已定（渲染归 Mac）；**C06/C08 NOT_STARTED**（选型评审项，不虚报）
### D 队列（标准交付）10/10 核心完成
D01 前置（B02-W019 继承）· D02 ✅（RFC 3161 真协议+签发时证书审计）·
D03 ✅ · D04 ✅（evpack/2.0）· D05 ✅（流式 ZIP64）· D06 ✅（六模板）·
D07 ✅ · D08 ✅ · D09 ✅（离线网页验证器）· D10 ✅（8 格式互操作）
### E 队列（iOS 参考）——未启动（第五优先级，依赖 C/Mac 审计）
### F 队列（性能可靠）核心 6/10
F01 ✅ · F02 ✅ · F04 ✅ · F05 ✅ · F06 ✅ · F08 ✅ · F10 ✅（基线 JSON）；
F03/F07/F09 部分（复用既有 TM45/TM91/TM32 注入面）
### G 队列——未启动（第六优先级，不阻塞首发）
### H 队列（集成交付）
H09/H10 ✅ **首份 Mac Ready 冻结候选**（12 组件 manifest）· H02 ✅
（Integration 02 对拍+契约更新）· 其余 H 项持续更新中

## C. 真实缺陷修复（本轮累计 9 个）

1. EXIF 7/8 映射互换（C02 对拍发现，官方定义重推导钉死）
2. TM15 left/right 命名与标准 EXIF 6/8 对调（行为一致、标签互换，交叉
   对照测试钉死）
3. 隐私遮挡区域越界缺 x+w/y+h 校验（测试抓到）
4. ZIP64 标记语义（force_zip64 常开，标记必真）
5. WIN-HOTFIX-01 同钥注册 31s 锁等待（合并带入：同连接审计，P95 0.85ms）
6. W018 CLI 对 D05/TM05 约定文件误报孤儿（白名单为文档化约定）
7. quality_gate sensitive-scan 字符串字面量 PEM 误报（要求真实密钥体）
8. evpack 首版 manifest 后置导致 scratch 漏写内容（测试抓到，重构为
   manifest-first + scratch 两段式）
9. F08 上传测试误读 HOTFIX-02 双会话语义（409 为保护行为，断言修正
   并把真实合同钉进测试注释）

## D. Mac 交接（首份冻结候选）

`handoff/mac-ready/`：`integration-manifest.json`（12 组件机器可读，
candidate `b3d5df5`）+ `01-START-HERE.md`（按依赖排序的接入路径）。
Swift 全线 CROSS_PLATFORM_PENDING（未在本机编译，不虚报）；Mac
Protocol02 已真跑过 TM01/TM10/TM12 向量（1.015 数值一致）。

## E. 真机待验证（Mac 执行，Windows 永不代答）

TM55 十项清单 + hotfix live 流程 + App Attest 真 Apple 根交叉验证 +
TM22 校准 + TM11 GNSS 采集 + TM35 弱网真机复现 + 密钥轮换真机流程 +
真实证据包过 CLI/网页验证器。

## F. 产品决策待确认（DECISION_REQUIRED）

十维引擎接生产 verify 路由 · issue photo role 列与服务端历史表 · 原图
删除策略与配额 enforce · 多租户/商业化范围。

## G. 推送状态

**0 积压**：`ce7c641..b3d5df5` 已全量推送。历史 bundle 保留于
`backups/`（verify okay）。main 未动、无强推、无自动合并。

## H. 未完成与下一步

E 队列（iOS UI 参考 + XCUITest 套件）→ C06/C08 选型评审 → F03/F07/F09
深化 → G 队列 → H03-H08/H11-H12（RC3/RC4 候选 + 干净环境最终验收）。
