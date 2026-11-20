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

## 18 项技术状态总表（Wave 2 后，2026-11-20）

状态使用统一九态：NOT_STARTED / RESEARCHED / POC_READY /
DESKTOP_VERIFIED / DEVICE_PENDING / DEVICE_VERIFIED / LIMITED /
BLOCKED / REJECTED。**"Windows 测试通过"与"真机验证"严格分开：**
桌面已验证但依赖真机的项目，Windows 侧记 DESKTOP_VERIFIED 并在
备注列明确 DEVICE_PENDING 项。

| # | 技术 | Wave | 状态 | 证据（测试/基准/工具） | 待 Mac/iPhone |
|---|---|---|---|---|---|
| T01 | Secure Enclave 真机签名 | 1 | POC_READY | G1-G4 协议测试 + 真机验收脚本（experiments/t01） | SE/Keychain 实测（DEVICE_PENDING） |
| T02 | 离线 GNSS 与位置可信度 | 1 | POC_READY | 元数据 v2 + 8 测试（manual 永不冒充 verified） | Core Location 离线实测 |
| T03 | 可信时间与防篡改 | 1 | POC_READY | 6 类时钟异常检测 + 9 测试（confidence 上限语义） | —（纯算法） |
| T04 | LiDAR 工程测量精度 | 2 | POC_READY（工具链完整） | 132 条唯一 ID 清单 + 统计核心（W-02）+ 验收 CLI（W-03）；合成自测数据全标 SYNTHETIC | 全部实测数据（DEVICE_PENDING，唯一 test_id 已对齐 Mac CSV） |
| T05 | 原图/标注/水印分离 | 2 | DESKTOP_VERIFIED | W-10：伪造对象拒绝、字节/文件复验、替换删除检测、重启恢复（13 测试） | iOS 衍生图渲染 |
| T06 | 数据完整性与故障恢复 | 2 | DESKTOP_VERIFIED | W-04：F1-F8 真实文件故障矩阵全测（10 测试），恢复永不删数据 | iOS `.pending-` 机制对照实测 |
| T07 | 工程图纸与照片关联 | 3 | POC_READY | R2/Wave3：图纸修订哈希定版 + ≥3 点仿射校准（残差超限拒绝）+ pin 修订绑定 + placement/verification 分离 + GPS 佐证三要素（7 测试） | Mac：PDF 取点 UI；文本锚点自动迁移（v2 研究） |
| T08 | AR 测量与照片坐标同步 | 3 | POC_READY | R2/Wave3：内参/位姿/世界点持久化 + **第三方可重算投影**（0.5px oracle）+ 跟踪降级可见 + 跨会话合并禁止（9 测试） | Mac：ARKit 位姿序列化对齐（真机） |
| T09 | 完全离线多格式导出 | 2 | DESKTOP_VERIFIED | R2：**真实磁盘流式基准**（1000 张 195MB，进程 RSS 峰值 27.3MB、增量 3MB，照片零驻留）；DOCX/XLSX 图片嵌入 + **真实 Office 2007 实开验证 3/3**（Word/Excel COM：图片数、中文、元数据全过） | iOS 像素级渲染与 HEIC 编码 |
| T10 | 自定义水印与模板引擎 | 2 | DESKTOP_VERIFIED | W-07：声明式 Schema + 自定义字段 + 条件显示 + 防伪语义（14 测试） | iOS CoreGraphics 按布局契约渲染 |
| T11 | 离线项目数据库与搜索 | 2 | DESKTOP_VERIFIED | W-05：SQLite WAL + FTS5；1 万与 10 万条基准、分页/迁移/索引对比（11 测试，桌面数字） | iPhone 实机 SQLite 数字不得照抄桌面值 |
| T12 | 弱网同步与断点续传 | 3 | DESKTOP_VERIFIED | W003：**真实 FastAPI 端点**（`/upload/experimental`，磁盘持久化、重启恢复、乱序/幂等/整包校验、批量数/块数/字节限额、过期清扫、traversal 防护、生产禁用开关）12 端点测试 + 联调客户端 CLI + 全链路集成 | iPhone 客户端重试节奏实测 |
| T13 | 照片防替换与独立验证包 | 2 | DESKTOP_VERIFIED | W-08：证据包 v1 + 四类篡改检出 + 衍生图入包 + 重复打包防护 + 历史密钥（14 测试） | —（Windows 侧完备） |
| T14 | C2PA 兼容 | 3 | RESEARCHED→POC_READY（桥接层） | R2/Wave3：字段→C2PA 标准断言映射固化 + 跨系统哈希不变量（4 测试）；真签名需外部证书账户，如实未做 | Mac 侧 c2pa-swift SDK 集成 |
| T15 | AI 工程缺陷辅助识别 | 4 | NOT_STARTED | — | — |
| T16 | AI 语音记录与自动报告 | 4 | NOT_STARTED | — | — |
| T17 | 照片对比与变化检测 | 4 | NOT_STARTED | — | — |
| T18 | 跨平台独立验证工具 | 2 | DESKTOP_VERIFIED（Windows）/ macOS 待冒烟 | W-09：离线 CLI（无服务器/无网络）+ cannot_verify 语义 + 退出码 + 双平台用法说明（10 测试，子进程端到端） | macOS 一次冒烟运行（预期通过：纯 Python + UTF-8 已固化） |

状态变更时必须同步更新 `acceptance-matrix.md` 并附可执行测试命令。

## 关联文档

- [risk-register.md](risk-register.md) — 风险登记册
- [dependency-map.md](dependency-map.md) — 依赖关系图与开发顺序
- [compatibility-matrix.md](compatibility-matrix.md) — 现有实现兼容性盘点
- [acceptance-matrix.md](acceptance-matrix.md) — 验收矩阵（证据索引）
- [reports/](reports/) — Wave 报告与审计报告
- [decisions/](decisions/) — 技术决策记录 (ADR)
