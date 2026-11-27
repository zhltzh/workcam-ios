# WORKCAM-WINDOWS-TECH-MARATHON-03 · 恢复后统一研发报告（终版）

管理基线：`codex/workcam-windows-marathon-03-management`（Draft PR #25）
热修基线：`2a53712` / 报告 `5d9c61d` · Mac PR #24 `53440fe`
研发分支：`dev/windows-tm-geometry` · **冻结 HEAD：见下方 RC3 冻结段**
全量实测：**1602 passed / 3 skipped / 0 failed** · 门禁 **12/12 PASSED**（本报告生成前的最新一轮）

## 80 项恢复状态（证据对账后）

| 状态 | 数量 | 说明 |
|---|---|---|
| DESKTOP_VERIFIED | 48 | 每项引用真实测试文件 |
| XCODE_PENDING | 12 | E02-E10 全套参考 + B12 + DesignTokens 等，Mac 编译 |
| CROSS_PLATFORM_PENDING | 3 | macOS bootstrap、Swift 业务/同步包 |
| POC_VERIFIED | 2 | C2PA、租户隔离（隔离候选） |
| BLOCKED | 2 | Swift 工具链（4 次尝试）、Docker（无 Desktop）——如实 |
| IN_PROGRESS | 6 | F03 目录扫描、H01 SBOM、H05 切换评审、H06 冻结（本报告）、H12（本报告） |
| NOT_STARTED | 7 | C06/C08（选型评审未决）、G02-G08 中 5 项企业储备、H07 RC4 |

## 本恢复批（周五晚—周六凌晨）新增交付

### P0 Sprint
1. **管理分支合并 + 80 项证据对账**（`cc280a3`）：46→48 项 DV，每项写 SHA+测试文件名；非手写，`reconcile_m03_status.py` 生成
2. **PR #24 合同核对**：project_update/archive、issue 全生命周期、photo link/unlink CAS、游标持久化、拒绝回执——`test_win_hotfix.py` 全覆盖
3. **FE-D01 注册锁复验**：Windows replay P95=45ms（无 31s 平台）

### E 队列 10/10（基于 E01 真实行号审计）
- **E01** 审计：Integration-02 全 21 文件映射、5 个已有 a11y ID 钉死为 XCUITest 基线、重叠判定（Mac 保留编辑器/canvas/PDF；E02/E07/E08/E09 是真缺口）
- **E02** Design System：**继承 W014 tokens 原样**（w014-rc2，WCAG AA 141/141），方向 A Field Pro 冻结、B/C 保留；生成 DesignTokens.swift + HTML 预览（Playwright 真浏览器验证 12 徽章/6 色板/语义注释全渲染）
- **E03-E06**：CameraService 协议缝隙（唯一 Mac 适配点）、ProjectsRefModel 批量语义 + 空态/错误态、标注**差异绑定**（A06 活测过）、MeasurementCard（TM22 质量态 + 未校准诚实措辞）
- **E07/E08**：十维可信详情 UI（无总分、unknown 永不绿）+ 同步中心 ViewModel（把 Mac 内联逻辑抽成可测层、待同步≠服务器已确认写进 footer）
- **E09** 设置：缓存清理 vs 原图保护**结构性红线**（derivative-only 类型系统 + 无删除按钮直到产品决策）
- **E10** XCUITest 基线：5 个现有 ID 钉死 + 3 个红线流程测试，全部 XCTSkip 诚实标注

### 红队 + 验证突破
- **FE-A12 十攻击红队全过**（`6238919`）：跨项目已验证状态不可迁移等不变量精确化
- **FE-G09 真浏览器**（`e36caa9`）：**抓到 2 个真实缺陷**——`.evpack` 扩展名门 + **DER 签名直传 Web Crypto 未转 raw r‖s**（任务书点名的跨端陷阱）
- **FE-I09 双 flake 根治**：门禁加失败留名日志 → 抓到 `resume_cost` 墙钟序断言（设计级非确定性）→ 功能不变量替代

### F 队列
- **F07 soak runner**（`c7fe170`）：真实 30 秒混合负载 0 错误、增长单调（照片/事件 append-only 钉死）、DESKTOP_SOAK 标签

## RC3 稳定候选冻结（FE-H06）

**冻结点：本报告提交的 SHA（见下方 Git 段）**，分支 `dev/windows-tm-geometry`。
- 组成：W001-W013 基线 + RC2 集成（`31917e3`）+ B02 全部（`d543e9a`）+ hotfix 01-04（`cd1219b`）+ TM 批次（TM01-TM55 核心）+ M03 批次 + 本恢复批
- 门禁：12/12 PASSED @ 1602 tests
- **排除**（保持隔离）：十维生产路由切换（评审中）、检查点锚定（候选）、C06/C08（评审未决）、G02-G08 企业储备、RC4 高级测量
- **不合并 main**；PR 由 Draft #25 → 提审

## H11 干净验证状态

fresh clone 验证上次通过于 `8d79c20`；本轮以**门禁 12/12 @ 1602** 作为等效全验（bootstrap 链未变——requirements/migrations/OpenAPI 无漂移，由门禁逐项钉死）。

## Mac 接入（不变）

`handoff/mac-ready/`：integration-manifest.json（12 组件）+ 01-START-HERE + DEVICE-ACCEPTANCE-KIT（用户可直接操作）。
**XCODE_PENDING 项接入路径**：`swift test --package-path swift/WorkCamCore` → 采用 E07/E08/E09 参考视图 → 按 E10 基线补 a11y ID。

## 真机待验证（Mac，Windows 永不代答）

TM55 十项 + hotfix live 流程 + App Attest 真 Apple 根 + TM22 校准 + TM11 GNSS + 弱网真机 + 轮换真机 + 真实证据包过验证器。

## 产品决策待确认

十维引擎接生产 verify 路由 · issue photo role 列 + 服务端历史表 · 原图删除策略/配额 enforce · C06/C08 选型 · 多租户范围。

## Git 状态

全部提交已推远端（0 积压）；main 未动；无强推。bundle 备份最新至 `cc280a3`（验证 okay）。

## 下一步

1. Mac 编译 XCODE_PENDING 全套（一条命令）→ 回报结果更新状态
2. C06（配准）/C08（OCR）选型评审文档（设计级，1-2 小时量）
3. H07 RC4 分支分离（高级测量隔离集成）
4. G02-G08 企业储备参考（P2，不阻塞首发）
