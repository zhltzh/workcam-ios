# WorkCam 技术攻坚总报告（WC-TECH-ALL-001 → WC-W-TECH-003-R1）

日期：2026-11-21 · 分支 `dev/windows-tech-lab-wave2`（PR #10，head 见 git log）
全量测试：**358 passed / 0 failed**（Windows 桌面；PR #7 合并后再 +18）
状态值仅使用九个法定值；"Windows 测试通过"与"iPhone 真机验证"全程分开。

## 18 项技术最终状态

| # | 技术 | 最终状态 | Windows 侧交付 | 待 Mac/iPhone |
|---|---|---|---|---|
| T01 | Secure Enclave 真机签名 | **POC_READY**（协议层） | G1-G4 生命周期协议（用真实 key_registry/time_anchor 服务验证）+ 8 步真机验收脚本 | SE 签名/Keychain/卸载实测（DEVICE_PENDING） |
| T02 | 离线 GNSS 与位置可信度 | **POC_READY** | 元数据 v2（source/quality/simulated）；manual 永不显示 verified；离线 network 封顶 C 级 | Core Location 离线分布实测 |
| T03 | 可信时间与防篡改 | **POC_READY** | 6 类时钟异常检测；confidence 上限语义（无锚点=low，最高 medium） | —（纯算法） |
| T04 | LiDAR 工程测量精度 | **POC_READY**（工具链） | 132 条唯一 ID 清单（68+32+32）+ 统计核心 + 验收 CLI | **全部实测数据**（DEVICE_PENDING） |
| T05 | 原图/标注/水印分离 | **DESKTOP_VERIFIED** | write-once 原图；伪造对象拒绝；磁盘篡改检测；重启恢复 | iOS 衍生图渲染 |
| T06 | 数据完整性与故障恢复 | **DESKTOP_VERIFIED** | F1-F8 真实文件故障矩阵；恢复永不删数据 | iOS `.pending-` 断电对照 |
| T07 | 工程图纸与照片关联 | **POC_READY** | ≥4 点校准（3 解+1 验证；共线/超残差/NaN 拒绝）；placement/verification 分离；GPS 级佐证 | PDF 取点 UI；向量对拍 |
| T08 | AR 测量与照片坐标同步 | **POC_READY** | 第三方可重算投影（0.5px oracle）；显式 FocalUnits；ARKit 转换闭式+回放向量 | ARKit 位姿序列化实测 |
| T09 | 完全离线多格式导出 | **DESKTOP_VERIFIED** | 真实磁盘流式：1000 张 195MB，进程 RSS 27.3MB（增量 3MB）；DOCX/XLSX 图片嵌入 + **真实 Office 2007 实开 3/3** | iOS 编码/渲染；Office 365/WPS 未验证不外推 |
| T10 | 自定义水印与模板引擎 | **DESKTOP_VERIFIED** | 声明式 Schema + 自定义字段 + 条件显示 + 防伪语义 | CoreGraphics 渲染一致性 |
| T11 | 离线项目数据库与搜索 | **DESKTOP_VERIFIED** | SQLite WAL+FTS5；10 万条基准（bulk 10.8s/备份 1.9s） | iPhone SQLite 数字需重测 |
| T12 | 弱网同步与断点续传 | **DESKTOP_VERIFIED** | 真实 FastAPI 端点（磁盘持久化/重启恢复/**双重门** opt-in+生产否决/限额/清扫）+ 联调客户端；链路内 10 分块丢 8 补 8 字节级一致 | iPhone 客户端退避节奏 |
| T13 | 照片防替换与独立验证包 | **DESKTOP_VERIFIED** | 证据包 v1：四类篡改+断链+历史密钥全检出；重复打包防护 | —（完备） |
| T14 | C2PA 兼容 | **RESEARCHED**（桥接层 POC_READY） | 断言映射固化+哈希不变量；与正式 SDK 十项差异表；**非标准合规，已声明** | c2pa-swift 集成+证书账户（产品决策） |
| T15 | AI 工程缺陷辅助识别 | **POC_READY**（骨架层） | 合成基准+经典基线（细长度判据）+像素级指标；advisory 协议强制 | Core ML 模型+真实数据评测 |
| T16 | AI 语音记录与自动报告 | **POC_READY**（协议层） | 音频→转写→修正→报告全链溯源；防编造强制引用；append-only | on-device 识别质量实测 |
| T17 | 照片对比与变化检测 | **POC_READY**（骨架层） | 平移配准（窗口去均值）+差异热区；曝光 +30 零误报；限制随报告输出 | vImage 配准+真实照片对 |
| T18 | 跨平台独立验证工具 | **DESKTOP_VERIFIED**（Win）/ macOS 待冒烟 | 无服务器验签 CLI；cannot_verify 永不显示为通过；字节级锚点对拍 | macOS 一次冒烟 |

## 产品化建议（逐项"能否进入产品开发"）

- **可直接冻结进产品**：T05/T06/T09（导出引擎层）/T10 Schema/T11 数据层
  /T12 协议与端点语义/T13 证据包格式/T16 溯源协议/T18 CLI
- **协议冻结、实现待 iOS**：T02/T03/T07/T08/T15 评测底座/T17 行为合同
- **必须真机数据才能有结论**：T01/T04（及上述全部 DEVICE_PENDING 项）
- **产品阶段决策**：T14 证书注册
- **贯穿性红线**（协议层强制，不可绕过）：AI 输出 advisory only；无引用
  报告字段拒收；manual 定位永不呈现为验证位置；无真机数据状态不得改出
  DEVICE_PENDING；签名有效 ≠ 现场真实

## 量化成果（全部 Windows 桌面数字）

| 指标 | 数值 |
|---|---|
| 全量测试 | 358 passed / 0 failed（main 基线 139 + techlab 219） |
| 导出流式契约 | 1000 张 195.3MB → 进程 RSS 峰值 27.3MB（照片零驻留；内联对照 >400MB） |
| Office 实开 | Word/Excel 2007：图片数、中文、元数据 3/3 |
| 数据库规模 | 10 万条 bulk 10.8s；FTS 中文 12-84ms；备份 1.9s/48.8MB |
| 上传链路 | 10 分块：丢 8 → 重启 → 补 8 → 字节级回传一致 |
| Wave 4 合成基准 | T15 recall 0.76/污渍零误报；T17 曝光 +30 零误报、平移精确找回 |

## 已知限制（全部如实）

1. **零真机数据**（T01/T04 及全部 DEVICE_PENDING 项）——工具、协议、
   验收脚本、原始数据清单已备齐，真机到位即插即用
2. T14 桥接非 C2PA 标准合规（差异表十项）；真签名需外部证书账户
3. T12 实验端点无鉴权（opt-in+生产否决双门下仅限可信网络）
4. DOCX/XLSX 显示尺寸固定；Office 365/WPS 未验证
5. T17 仅平移配准（视角/尺度是已声明盲区）；Wave 4 全部为合成数据
6. PR #7 合并前 main 少 18 项 W005 回归（合并后自然归一）

## 流程性成果

- **审计防漂移**：W-01 修复"96 声称 vs 68 实际"清单造假级不一致，
  清单改由生成器+总数断言产出
- **基准防造假**：R2 修复 tracemalloc 掩盖输入驻留（引擎 2.31MB vs
  实际进程 >400MB）；C 盘临时写满事件如实记录非冒充回归
- **协议防猜测**：T08 启发式改显式 units；T07 残差拒绝改 ≥4 点；
  T17 三次"貌似合理实则错误"的搜索面缺陷均被数值探针抓出并留档
- **文档防吹牛**：所有桌面数字标注 DESKTOP；每份报告含失败案例与限制
