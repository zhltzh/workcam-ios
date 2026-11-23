# Wave 0 审计报告

日期：2026-11-19 · 审计人：Windows 技术攻坚 · 基线：`main @ 17d9699`

## 1. 分支与 PR 状态

| 对象 | 状态 | 结论 |
|---|---|---|
| `main` | W001+W002+W003 后端、M001/M002 iOS 工程已合并 | 后端 157 项测试；iOS 为拍照原型 |
| PR #4 (W004) | open | 联调文档；**Swift 示例不作协议依据**（含已知错误，R3 修订待审） |
| PR #6 (M003) | draft | Evidence.swift 与后端协议经 W005-2 对照**兼容**；不覆盖、不合并 |
| PR #7 (W005+R1) | open（b2ea22d 已推送） | 联调工具 13/13 实测通过 |

## 2. 18 项技术能力盘点

结论已写入 [compatibility-matrix.md](../compatibility-matrix.md)。要点：

- **可信证据链（T01/T03/T13 的核心）已有坚实底座**：签名协议、证据分级、时间锚点 v2、重放检测、跨语言向量、联调自检全部落地且有自动化测试。
- **定位（T02）是最薄弱的"已有"能力**：仅 4 个数值字段，无来源与质量语义，直接违反"不得把人工输入当可信 GPS"原则 → 本轮 Wave 1 首先补齐。
- **时间异常检测（T03）缺纯算法层**：锚点只证明上界，回拨/跳变/时区异常无检测 → 本轮补齐。
- **T05 是下游 T09/T10/T13/T18 的共同上游**且完全空白 → 本轮补数据模型，防止 Wave 2 在错误地基上开工。
- **T04 是唯一真机强依赖**：Windows 侧只能出方案+分析工具，已按此拆分。

## 3. 禁止事项确认

- 不重做签名协议/锚点/向量（已有 157 项测试覆盖）。
- 不接入生产路径：实验代码独立放 `backend/techlab/`，不注册路由。
- 不动 PR #4/#6/#7 的分支（本 PR 基于 main 新建 `dev/windows-tech-lab`）。

## 4. Wave 0 交付物

- `docs/tech-lab/`：README（18 项状态总表）、risk-register、dependency-map、compatibility-matrix、acceptance-matrix、本报告。
- `tools/tech-lab/`、`test-vectors/tech-lab/`、`backend/techlab/`、`backend/tests/test_techlab/` 目录骨架。
- ADR-0001（密钥生命周期）、ADR-0002（原图/衍生图分离）。

## 5. 进入 Wave 1 的依据

Wave 1 目标（T01/T02/T03/T04/T05）中除 T04 真机部分外，全部可在 Windows 侧完成 PoC + 自动化测试。真机相关交付（实验方案、验收脚本、分析工具）同步备齐，Mac 真机到位即可执行。
