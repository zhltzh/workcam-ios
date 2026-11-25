# WORKCAM-WINDOWS-BATCH-02 · 统一任务板

- 批次：BATCH-02（V2 总控指令，2026-11-25 06:17 生效）
- 状态体系：NOT_STARTED / IN_PROGRESS / IMPLEMENTED / DESKTOP_VERIFIED / DEVICE_PENDING / BLOCKED
- 每项任务报告六要素：批次编号 / 完整任务编号 / 基线 SHA / 分支名 / 交付 Commit SHA / PR 状态
- 本文件由 00 总控维护，各 worktree 会话只读。
- 参考基线：`31917e3`（W013-R1，= 645 passed 全量回归基线）
- main：`17d9699`（未动，本轮不自动合并 main）

## 编号体系映射（防同号不同内容）

同一数字在两条线含义不同，凡报告必须带 `B02-` 前缀：

| 数字 | 历史线（2026-11-24 夜间批次） | B02 线（本批次正式任务） |
|---|---|---|
| W014 | UI/UX 原型 ✅ 已完成（`f77d083`+`23ca20a`） | 工程问题离线同步（P0） |
| W015 | RC2 集成冻结（未启动，职能并入 B02-W022） | 工程检查报告增强（P1） |
| W016 | 场景模拟与压测 ✅ 已完成（`b36430d`+`283a10c`，645 passed） | LiDAR 测量分析工具升级（P0） |
| W017 | Swift 参考实现（未启动，职能分散并入 B02-W026 与 B02-W014 的 Swift 交付物） | 工程照片批量管理（P1） |
| W018 | 数据可靠性与灾备（未启动；联合备份恢复测试已随历史 W016 基线就绪） | 独立可信证据验证 CLI（P0） |
| W019 | 官网与发布准备（未启动） | C2PA 兼容性研究（P1） |
| W020 | 全项目审计交接（未启动） | 大规模数据与性能压测（P1） |

历史线未启动任务不再按旧编号执行。

## B02 任务总表

| 编号 | 研发组 | 优先级 | 状态 | 分支 | 交付 SHA | PR | 验收 |
|---|---|---|---|---|---|---|---|
| B02-W014 离线同步 | 01 后端 | P0 | DESKTOP_VERIFIED | dev/windows-b02-w014-offline-sync | `80b4bc1`+`e40792a` | BLOCKED(gh) | 全量 644 passed，总控复跑 12 passed |
| B02-W015 报告增强 | 01 后端 | P1 | DESKTOP_VERIFIED | dev/windows-b02-w015-report-enhance | `3f84f35`+`dc1eb9a` | BLOCKED(gh) | 全量 648 passed，总控复跑 16 passed；8 样例（7 PDF+1 ZIP）真实存在 |
| B02-W016 LiDAR 分析 | 02 算法 | P0 | DESKTOP_VERIFIED（真机 DEVICE_PENDING） | dev/windows-b02-w016-lidar-stats | `89f2f81`+`102b49b` | BLOCKED(gh) | 全量 654 passed，总控复跑 22 passed。FINDING-W016-1：matplotlib 可选依赖未声明（W021 审出，待修） |
| B02-W017 批量管理 | 01 后端 | P1 | DESKTOP_VERIFIED | dev/windows-b02-w017-photo-batch | `b96db5d` | BLOCKED(gh) | 全量 658 passed（多 seed），总控复跑 26 passed |
| B02-W018 验证 CLI | 02 算法 | P0 | DESKTOP_VERIFIED（真机包 DEVICE_PENDING） | dev/windows-b02-w018-evidence-verifier | `88d9e15`+`69f68e1` | BLOCKED(gh) | 全量 674 passed，总控复跑 42 passed |
| B02-W019 C2PA 研究 | 02 算法 | P1 | DESKTOP_VERIFIED（研究+PoC，生产零改动） | dev/windows-b02-w019-c2pa | `4ba6dc1` | BLOCKED(gh) | 基线 632 保持；PoC 8 份 JSON 实测证据；建议不进 RC2 |
| B02-W020 性能压测 | 03 测试安全 | P1 | NOT_STARTED | dev/windows-b02-w020-perf | — | BLOCKED(gh) | — |
| B02-W021 安全审计 | 03 测试安全 | P0 | DESKTOP_VERIFIED | dev/windows-b02-w021-security | `ad4e93b`+`92461fd`+`66d9e17` | BLOCKED(gh) | 全量 648 passed；16 项检查=13 pinned+3 已修复；三分支审查 PASS；总控复跑 16 passed |
| B02-W022 RC2 集成候选 | 00 总控 | P0 | NOT_STARTED | dev/windows-b02-w022-rc2 | — | BLOCKED(gh) | — |
| B02-W023 跨语言签名向量 | 02 算法 | P0 | DESKTOP_VERIFIED（Swift 侧待 Mac） | dev/windows-b02-w023-crosslang-sig | `ef5e004` | BLOCKED(gh) | 全量 743 passed；总控复跑 111 passed+校验器 144 checks |
| B02-W024 时间定位可信模型 | 02 算法 | P0 | DESKTOP_VERIFIED（真机 DEVICE_PENDING） | dev/windows-b02-w024-time-geo-trust | `1aef0c8`+`e3c3c2a` | BLOCKED(gh) | 全量 682 passed；总控复跑 50 passed；未接线生产路由 |
| B02-W025 存储与空间优化 | 01 后端 | P1 | IN_PROGRESS | dev/windows-b02-w025-storage | — | BLOCKED(gh) | — |
| B02-W026 API 契约工具 | 01 后端 | P1 | IN_PROGRESS | dev/windows-b02-w026-api-contract | — | BLOCKED(gh) | — |
| B02-W027 部署与 CI | 03 测试安全 | P1 | IN_PROGRESS | dev/windows-b02-w027-deploy-ci | — | BLOCKED(gh) | — |
| B02-W028 商业化技术基础 | 00 总控 | P2 | NOT_STARTED | dev/windows-b02-w028-commerce | — | BLOCKED(gh) | — |

PR 全局状态：gh 未登录（BLOCKED）。分支推送远端不受影响；待用户 `gh auth login` 后由总控统一补开 PR。

## 已冻结的历史成果（不计入 B02 工作量，不重做）

| 成果 | 分支 | Commit | 验收 |
|---|---|---|---|
| W013-R1 工程检查闭环 | dev/windows-inspection-w013 | `31917e3` | 635 passed，多 seed |
| 历史 W014 UI/UX 原型 | dev/windows-prototype-w014 | `f77d083`+`23ca20a` | WCAG 141/141，16 截图 |
| 历史 W016 场景压测 | dev/windows-stress-w016 | `b36430d`+`283a10c` | 645 passed，多 seed |
