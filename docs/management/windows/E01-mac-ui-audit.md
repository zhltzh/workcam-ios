# FE-E01 · Night 05 / PR24（Integration 02）UI 代码审计

**审计对象**：`origin/codex/workcam-mac-integration-02` @ `53440fe`（21 个 Swift 文件，~4200 行）
**审计方法**：逐文件读真实代码（本文件引用行号为该提交内行号）
**目的**：E02-E10 之前先建映射，避免重复开发 Mac 已有界面。

## 1. 现有视图树（事实清单）

| 视图 | 文件:行 | 功能 | 测试性 |
|---|---|---|---|
| `ContentView` | App/ContentView.swift:11 | 主导航：Sign/Camera 状态、Backend Sync、LiDAR Lab、Projects、Camera、Evidence 导出 | `status()` 辅助是硬编码占位（"Not Implemented"）——签名状态 UI **未接线** |
| `BackendSyncView` | App/ContentView.swift:100(私有) | 端点+注册码表单、项目选择、Queue/Publish、取消 | 业务在 `start(publish:)` 内联 + `ProjectSyncQueue`——**可测但没测**（UI 层） |
| `CameraView` | Features/Camera/CameraView.swift:58 | `CameraModel` 状态对象 + 网格/闪光/预览/审核 sheet | `CameraModel` 承载全部状态；AVFoundation 在 `CameraController`（独立）——**协议化好，可换桩** |
| `ProjectsView` | CameraView.swift:193 | 列表+搜索+归档过滤+新建项目 alert | 纯 ProjectStore 读写——**可测** |
| `ProjectPhotosView` | CameraView.swift:368 | 项目照片网格、重命名、导出/报告 | 同上 |
| `InspectionIssuesView`+`IssueEditor` | CameraView.swift:269/303 | 问题列表+编辑 sheet（severity/status picker） | Night05 生命周期闭环在——**Windows B04 模型已对拍** |
| `ProjectPhotoView` | CameraView.swift:506 | **标注编辑器**：canvas overlay、工具 picker（marker/arrow/rect/ellipse/text/freehand）、undo/redo 栈、编号、水印导出、证据状态行 | `updateAnnotations` 闭包式 undo/redo——逻辑可直接对照 TM63 合同 |
| `LidarLabView` | Features/TechLab/LidarLabView.swift:239 | raycast/scene_depth/smoothed 三模式采样、批量 CSV、条件记录 | `LidarLabModel` 与 session 强耦合——**Windows 分析器只能接收 CSV** |

## 2. 现有 Accessibility Identifiers（XCUITest 基线）

已存在的稳定 ID（Mac 已用，**FE-H12 必须以此为准**）：
`cameraStatus`(:107)、`projectArchiveFilter`(:232)、`projectPhotoRow`(:423)、`annotationCanvas`(:570)、`annotationToolPicker`(:574)

**缺口**（E10 需与 Mac 协调新增）：项目创建/搜索框、同步状态、冲突提示、报告导出、证据十维详情——目前无 ID。

## 3. 与 Windows 交付的重叠判定（不重复开发）

| Windows 交付 | Mac 现状 | 判定 |
|---|---|---|
| B01 模板/清单 | **无**（ProjectsView 只有名字） | Mac 全缺 → E 端做 Swift 参考有价值 |
| B04 生命周期 | `IssueEditor` 全套已实现（含 before/after 角色） | **Mac 已有 → E05 只做差异参考**，不重写 |
| TM63 标注几何 | `ProjectPhotoView` 自带 canvas/undo/redo | **Mac 已有渲染**；Windows 只交几何合同（已完成）→ E 端不重写编辑器 |
| 水印 | `watermark(photo:annotations:template:)` :744 Mac 端实现 | Windows TM64 纯布局引擎**补充**而非替代 |
| 报告 PDF | `report(project:photos:paper:kind:)` :841 Mac 端生成 | Windows D06 是后端版——**双端各自保留**，合同由 D10 对拍 |
| 同步 | `SyncOutbox`(41行，模型) + `WorkCamAPI`(394行) | Windows Swift 包与其**对拍已完成**(H02)；E08 同步中心 UI Mac **没有** → 有价值 |
| 证据详情十维 | ContentView `status("Signing Status")` = 占位 | **Mac 全缺** → E07 高价值 |
| 相机 | `CameraController` 162 行封装 AVFoundation | E03 协议化已在 Windows 包中定义——对齐即可 |

## 4. 业务逻辑耦合与测试性诊断

1. **`ContentView.start(publish:)`（BackendSyncView）**：endpoint URL 解析、任务编排、错误消息全部内联视图——**建议**抽 ViewModel（E08 参考会给出）。
2. **`CameraView` 1052 行单文件**：视图+模型+渲染工具+报告+水印+导出全在一文件。`CameraModel` 可测，静态渲染工具（image/watermark/report/delivery :734-930）**与视图耦合但纯函数**——可逐个搬进可测模块。
3. **`EvidenceStore.report()` 直接进 UI**：没有中间层——十维详情(E07)在 Windows 参考里以 TrustReport 结构给出，Mac 可直接吃。
4. **无单元测试目标**：Tests/ 下是 `main.swift` 风格脚本（断言用 precondition），不是 XCTest target。TM01/TM02/TM10/TM12 合同已在 `Protocol02/main.swift` 真跑过——**这条链路是好的**；但 UI 层零测试。

## 5. E02-E10 的执行结论（基于本审计）

- **高价值（Mac 全缺）**：E02 Design System、E07 十维证据详情、E08 同步中心、E09 设置/数据管理
- **协议对齐即可（Mac 已有）**：E03 相机（对齐 CameraController 协议）、E05 标注/整改（差异参考）、E06 测量 UI（ARKit 留 Mac）
- **基线依赖**：E10 XCUITest 必须先与 Mac 协商新增 a11y ID（本审计第 2 节清单）
- **明确不做**：不重写 IssueEditor、标注 canvas、Mac 端 PDF 渲染——只交合同与差异参考

## 6. 状态

E01 → DESKTOP_VERIFIED（审计基于真实代码行号；无代码变更，产出为本文件）。
E02-E10 解锁，按第 5 节优先级执行。
