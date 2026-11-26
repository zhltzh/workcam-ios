# WorkCamCore · Swift Package（TM56/TM58/TM61/TM65/TM88 Swift 侧）

## 状态（如实声明）

- **Windows 无 Swift 工具链**：官方 `Swift.Toolchain 6.4.3` 经 winget
  两次静默安装失败（MSI 0x80070643，C 盘空间不足以承载工具链），
  已如实记录 BLOCKED。本包全部 Swift 源码为 **CROSS_PLATFORM_PENDING**
  状态：Windows 完成了与 Python 参考实现的逐函数移植与共享向量，
  **但从未在 Windows 上编译或运行**——Windows 不声称任何 Swift 测试通过。
- Python 侧（几何 85 tests + TM88 向量 35 cases）已 DESKTOP_VERIFIED。
- **Mac 首次接入时：`swift build && swift test --package-path
  swift/WorkCamCore`，把真实结果回传记录。**

## 模块边界（TM56 验收项）

| Target | 内容 | 依赖 |
|---|---|---|
| WorkCamGeometry | TM15 相机几何/深度反投影/EXIF 方向/裁剪缩放、TM16/TM20 距离角度坡度、TM17 平面拟合(LSQ/PCA/RANSAC+Jacobi 特征)、TM18/TM19 面积体积、TM22 质量与误差传播、TM13 大地测量 | 无（纯 Foundation） |
| WorkCamModels | TM58 十维可信引擎（客户端策略：只解析/展示/有限本地验证，绝不提升可信等级） | 无 |
| WorkCamEvidence | TM57 证据协议模型 + canonical payload 构造器（字节合同 = TM01/TM02）+ 六层验证结果模型。**密码学不自行实现**——验证走 CryptoKit（Mac 侧），本包只含纯文本合同 | 无 |
| WorkCamTime | TM59 时间区间引擎（锚点/重启→UNKNOWN/墙钟只作信号/过期降级）+ ClockSource 注入协议 | 无 |
| WorkCamLocation | TM60 定位质量（精度分带/陈旧/瞬移/海拔跳变/手工永不评级） | WorkCamGeometry |

**平台纪律**：全部 target 仅 import Foundation（WorkCamLocation 例外地
依赖 WorkCamGeometry）。无 UIKit/SwiftUI/ARKit/CoreLocation 依赖——
Apple 专有能力一律经协议注入（ClockSource 等）。

## 与 Python 的跨语言合同（TM88）

- 黄金向量：`test-vectors/geometry/geometry-golden-vectors.json`
  （version `geometry-vectors/1.0`，34 cases；本包测试资源内为**逐字节
  副本**，版本不符测试即失败——两侧永不静默漂移）。
- Python 消费测试：`backend/tests/test_techlab/test_tm88_geometry_vectors.py`
- Swift 消费测试：`Tests/WorkCamCoreTests/GeometryGoldenVectorTests.swift`
- RANSAC 种子注意：Swift 用 xorshift64*，Python 用 `random.Random(seed)`
  ——两者采样序列不同步。向量因此只钉**种子稳定的结论**（内点数/比例/
  重拟合法向），不钉采样中间态；测试端按此断言。

## Mac 接入步骤

1. `git clone` 本仓库，检出本分支；`cd swift/WorkCamCore`
2. `swift build`（Swift 5.9+ 工具链；无第三方依赖）
3. `swift test`——预期：黄金向量 34 cases + 十维规则 11 条全绿
4. 向量更新流程：Windows 侧改 `backend/tools/tm88_gen_geometry_vectors.py`
   并重跑 → 提交新 JSON → 复制进 `Tests/WorkCamCoreTests/Resources/`
   （版本号递增，两侧同时更新）
5. App 内接入：按 target 逐库引用；Apple 专有层（相机/定位/时钟）实现
   包内协议（ClockSource 等）后注入。

## 已知限制

- Swift 侧未经任何编译器验证（CROSS_PLATFORM_PENDING）；预期会有少量
  语法级修正，属正常接入成本，记录后即可。
- `classifySurface` 的 z-up 法向规约使地面/天花板不可区分——需相机
  位姿上下文（已在 doc 注释与 Python 测试中说明）。
- TM57 只含模型与 canonical 构造器；签名验证本体在 Mac 用 CryptoKit
  实现（禁止自行实现生产密码学原语——开发书纪律 #17）。
