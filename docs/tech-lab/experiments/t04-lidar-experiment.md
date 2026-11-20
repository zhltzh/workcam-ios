# T04 — LiDAR 工程测量精度真机实验方案

状态：RESEARCHED（方案+分析工具就绪）→ 待 Mac 真机执行
Windows 侧交付：本方案 + `tools/tech-lab/benchmarks/lidar_error_analysis.py`（自带合成数据自测）

## 1. 实验目标

回答一个未预设结论的问题：**iPhone LiDAR（ARKit SceneDepth/Raycasting）在 0.5–5 m 工程距离上到底能达到什么精度、什么成功率？**

不做任何"工程测量精度达标"的预设。失败场景与正常场景同权重记录。

## 2. 实验设备与环境

- iPhone Pro（LiDAR 硬件必需；模拟器无 SceneDepth）
- 标定 setup：激光测距仪或卷尺作为 ground truth（±2 mm 级），三脚架固定
- 硬质平整标靶（A3 白板 + 纹理贴图两版）
- 场地：室内 ≥ 6 m 直线空间；可加桌面/楼道场景

## 3. 测试矩阵

| 条件 | 距离点 | 重复次数 | 预期风险 |
|---|---|---|---|
| normal（纹理标靶、日光灯） | 0.5 / 1 / 2 / 3 / 5 m | ×4 | 基线 |
| low_light（关灯/遮光） | 1 / 2 / 3 / 5 m | ×4 | 深度丢失 |
| glass（玻璃面） | 1 / 2 m | ×4 | 穿透/跳变 |
| oblique（标靶倾斜 45°） | 1 / 3 m | ×4 | 角度偏差 |
| textureless（纯白板） | 1 / 3 m | ×4 | 特征不足 |
| reflective（镜面/金属） | 1 / 3 m | ×4 | 多路径 |

共 96 次测量。每次记录成功读数或**显式失败**（失败绝不省略）。

## 4. Swift 最小实验代码骨架（Mac 执行，真机编译）

```swift
import ARKit
import RealityKit

final class DepthMeter: ARSessionDelegate {
    // Raycasting 主路径：单点、低耗电、现场可用性最好
    func measure(at point: CGPoint, in view: ARView) -> MeasurementResult? {
        guard let query = view.makeRaycastQuery(from: point,
                                                allowing: .estimated,
                                                alignment: .horizontal),
              let result = view.session.raycast(query).first else {
            return nil   // ← 失败也返回：counted failure
        }
        let distance = -result.distance  // meter
        return MeasurementResult(distance: distance, worldTransform: result.worldTransform)
    }
    // 备选路径（Wave 3 再做）：ARMeshManager / SceneDepth .depthMap
}
```

导出格式 = `lidar_error_analysis.py` 的输入 CSV（列定义见工具 docstring）。

## 5. 执行步骤（Mac 真机）

1. 激光测距仪测真值 → 记入 `distance_true_m`。
2. iPhone 固定于三脚架，Raycasting 单点测量 → `distance_measured_m`（无读数留空）。
3. 每条件×距离×4 次重复，填满 96 行。
4. CSV 存 `tools/tech-lab/datasets/t04-lidar-raw-<device>.csv`。
5. Windows 侧运行分析：
   `python tools/tech-lab/benchmarks/lidar_error_analysis.py <csv> --json report.json`

## 6. 验收判定（数据说话）

| 指标 | 进入产品开发的门槛 |
|---|---|
| normal 条件 mean abs error | 待真机数据定标（预期 cm 级，但**以实测为准**） |
| normal 失败率 | < 5% |
| low_light/glass/reflective | 允许 LIMITED 标注 + 明确限制清单 |
| 可重复性（repeatability stddev） | 与 mean error 同数量级或更小 |

## 7. 已知限制

- LiDAR 有效范围约 0.25–5 m，超程必失败（>5 m 不在本实验范围）。
- ARKit 深度为**估计值**（`alignment: .estimated`），不承诺测绘级。
- 玻璃/镜面/吸光材质失败是物理限制，非实现缺陷——产品需 UI 预警而非掩盖。
- 本方案不预设达标结论；若实测不达工程级，状态记 LIMITED/REJECTED 并给替代方案（外接蓝牙测距仪做 ground truth + 照片记录）。
