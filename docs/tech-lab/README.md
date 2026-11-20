# WorkCam 技术攻坚实验室 (Tech Lab)

Windows 侧共性技术预研、实验实现、自动化测试与跨平台验证工具。
实验代码不接入生产路径；一切结论以可重复执行的测试为准。

## 目录

| 路径 | 用途 |
|---|---|
| `docs/tech-lab/` | 研究结论、决策记录、实验报告 |
| `backend/techlab/` | 后端实验模块（独立于 `app/` 生产包，不注册路由） |
| `backend/tests/test_techlab/` | 实验模块的自动化测试 |
| `tools/tech-lab/validation/` | 跨平台验证工具 |
| `tools/tech-lab/benchmarks/` | 性能与精度基准 |
| `tools/tech-lab/datasets/` | 测试数据集 |
| `test-vectors/tech-lab/` | 实验测试向量 |

## 统一状态定义

| 状态 | 含义 |
|---|---|
| NOT_STARTED | 尚未开始 |
| RESEARCHED | 研究完成（结论+资料来源已记录） |
| POC_READY | 实验代码完成（自动化测试通过） |
| DESKTOP_VERIFIED | 桌面环境验证完成 |
| DEVICE_PENDING | 桌面已验证，等待 iPhone 真机 |
| DEVICE_VERIFIED | 真机验证完成（附原始数据） |
| LIMITED | 仅部分场景可用（限制已记录） |
| BLOCKED | 技术受阻（阻塞原因+证据已记录） |
| REJECTED | 不满足目标要求（证据+替代方案已记录） |

**Windows 测试通过 ≠ iPhone 真机验证通过。** 无真机结果最多标记 DEVICE_PENDING。

## 18 项技术状态总表（Wave 0 审计基线）

| # | 技术 | Wave | 状态 | 现有基础（main / 开放 PR） | 本轮新增 |
|---|---|---|---|---|---|
| T01 | Secure Enclave 真机签名 | 1 | RESEARCHED | P-256/DER 协议+向量（W001/W002）；Keychain 注册（M003 PR#6）；157 项回归 | 密钥生命周期协议测试（`techlab/key_lifecycle.py`） |
| T02 | 离线 GNSS 与位置可信度 | 1 | POC_READY | 仅 4 字段 LocationData（W002） | 定位元数据结构 v2 + 质量评估工具 + 测试 |
| T03 | 可信时间与防篡改 | 1 | POC_READY | 时间锚点 v2（W003）；device_reported 分级（W002） | 时钟回拨/跳变/时区异常检测工具 + 测试 |
| T04 | LiDAR 工程测量精度 | 1 | RESEARCHED | 无 | 真机实验方案（`experiments/t04-lidar-experiment.md`）+ CSV 分析工具 |
| T05 | 原图/标注/水印分离 | 1 | POC_READY | 无（M003 仅签名原图） | 衍生图数据模型 + 防覆盖测试 |
| T06 | 数据完整性与故障恢复 | 2 | LIMITED | 后端原子写+回滚（W003-R3）；iOS `.pending-` 隔离（PR#6） | Wave 2 系统化故障注入 |
| T07 | 工程图纸与照片关联 | 3 | NOT_STARTED | 无 | — |
| T08 | AR 测量与照片坐标同步 | 3 | NOT_STARTED | 无 | — |
| T09 | 完全离线多格式导出 | 2 | NOT_STARTED | 无 | — |
| T10 | 自定义水印与模板引擎 | 2 | NOT_STARTED | 无（T05 先行，见依赖图） | — |
| T11 | 离线项目数据库与搜索 | 2 | NOT_STARTED | 无 | — |
| T12 | 弱网同步与断点续传 | 3 | NOT_STARTED | 无 | — |
| T13 | 照片防替换与独立验证包 | 2 | RESEARCHED | 签名绑定哈希（W001）；记录存储+重放检测（W002-R3） | Wave 2 证据包格式 |
| T14 | C2PA 兼容 | 3 | NOT_STARTED | 无 | — |
| T15 | AI 工程缺陷辅助识别 | 4 | NOT_STARTED | 无 | — |
| T16 | AI 语音记录与自动报告 | 4 | NOT_STARTED | 无 | — |
| T17 | 照片对比与变化检测 | 4 | NOT_STARTED | 无 | — |
| T18 | 跨平台独立验证工具 | 2 | LIMITED | `tools/integration_check.py`（需联网+后端进程） | Wave 2 离线独立验签工具 |

状态更新时必须同步更新 `acceptance-matrix.md` 并附测试证据。

## 关联文档

- [risk-register.md](risk-register.md) — 风险登记册
- [dependency-map.md](dependency-map.md) — 依赖关系图与开发顺序
- [compatibility-matrix.md](compatibility-matrix.md) — 现有实现兼容性盘点
- [acceptance-matrix.md](acceptance-matrix.md) — 验收矩阵（证据索引）
- [reports/](reports/) — Wave 报告与审计报告
- [decisions/](decisions/) — 技术决策记录 (ADR)
