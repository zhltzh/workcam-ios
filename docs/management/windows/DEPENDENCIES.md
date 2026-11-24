# BATCH-02 · 任务依赖关系

## 依赖图

```
B02-W014 离线同步 ──┐
B02-W015 报告增强 ──┼─（弱依赖 W012/W013 已有基线，可并行）→ B02-W022 RC2 集成
B02-W016 LiDAR ────┤
B02-W017 批量管理 ─┤
B02-W018 验证 CLI ─┤
B02-W019 C2PA ─────┤
B02-W020 性能压测 ─┤（建议在主要功能合入后压测，避免重复跑）
B02-W021 安全审计 ─┤（持续线：各任务交付后即时回归；终轮全面审计在 W022 前）
B02-W023 跨语言签名 ┤（W022 的 Swift/Python 一致性验收依赖它）
B02-W024 时间定位 ─┤
B02-W025 存储 ─────┤
B02-W026 API 契约 ─┤（Swift 结构核对依赖 W014/W023 的 Codable 交付物）
B02-W027 部署 CI ──┘（CI 配置依赖被测任务存在，但可先行搭骨架）
B02-W028 商业化研究 ── 独立，不阻塞 W022，P2
```

## 关键依赖说明

- **B02-W022（RC2 集成，总控）**：最终候选必须明确列出已审查纳入的任务；未完成的记 BLOCKED/NOT_STARTED，不得虚报集成。
- **B02-W021（安全审计）**：对其他组交付做独立回归，不与被审任务共享 worktree。
- **B02-W026**：接口契约文档须与实际实现一致；Swift Codable 部分引用 B02-W014 与 B02-W023 的输出做交叉核对。
- **B02-W016/W024**：真实 iPhone 数据未到位，Windows 侧交付分析器/协议/合成验证，状态 DEVICE_PENDING，不阻塞桌面部分验收。
- **B02-W018**：Mac 真实证据包未提供前用公开测试向量，标记真实设备交叉验证待完成。

## 复用基线（先读再写，禁止重复开发）

| 既有能力 | 位置 |
|---|---|
| W008 同步与冲突恢复引擎 | backend + docs/mvp/w008-conflict-policy.md |
| W013-R1 sync batch 8 操作与 id_map | docs/mvp/w013-r1-acceptance.md |
| W013 标注/Issue 数据模型 | docs/mvp/w013-annotation-spec.md |
| LiDAR 统计核心（tech-lab W-02） | tools/tech-lab/ |
| 离线验证 CLI 基础（T18）/证据包（T13） | tools/tech-lab/ |
| 公开测试向量 | backend/generate_test_vectors.py + test-vectors/ |
| 备份恢复测试基线 | 历史 W016 验收文档第 7 节所列 5 项测试 |
| 设计 tokens 与 SwiftUI 映射 | dev/windows-prototype-w014: design/ |
| 场景模拟测试手法 | dev/windows-stress-w016: backend/tests/test_w016_scenario.py |
