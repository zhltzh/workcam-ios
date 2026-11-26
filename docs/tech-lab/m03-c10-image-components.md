# M03-C10 · 图像处理统一组件索引

Python 参考实现（`backend/techlab/`）+ Swift 移植状态一览。
集成门禁纪律照旧：`tools/quality_gate.py` 的 isolation 门禁保证
`app/` 不依赖实验模块。

## 组件清单

| 组件 | Python 参考 | 测试 | Swift | 状态 |
|---|---|---|---|---|
| 水印布局（C01 极端强化） | `geometry/watermark_layout.py` + `tools/m03_c01_c02_gen_vectors.py` | 56（含 40 EXIF + 9 极端） | `WorkCamGeometry/WatermarkLayout.swift` | DESKTOP_VERIFIED；Swift CROSS_PLATFORM_PENDING |
| 标注几何（C02 全 EXIF） | `geometry/annotations.py` + `geometry/camera.py` | 17 + 交叉对照 | `WorkCamGeometry/Annotations.swift` | DESKTOP_VERIFIED；**C02 发现：TM15 left/right 命名与标准 EXIF 6/8 对调**（行为一致、标签互换，已钉死交叉对照测试） |
| 点云（TM21） | `geometry/pointcloud.py` | 17 | 计划中 | DESKTOP_VERIFIED |
| 质量检测（C04） | `business/photo_quality.py` | 9 | 计划中 | DESKTOP_VERIFIED（**建议专用**，免责声明恒附加） |
| 重复候选（C05） | `business/photo_quality.py`（aHash/dHash/SHA-256） | 6 | TM72 PhotoIndex 已有重复候选语义 | DESKTOP_VERIFIED（near≠exact 结构性分离） |
| 隐私遮挡（C09） | `business/privacy_redaction.py` | 9 | 计划中 | DESKTOP_VERIFIED（仅衍生图；人挂区域；检测器只建议） |
| 图像配准（C06） | — | — | — | **NOT_STARTED**（特征匹配/单应性：建议 Mac 侧 Vision+CoreImage 评估后定，Windows 不盲目手写 SIFT） |
| 对比组件（C07） | — | — | — | UI 参考（E07）承载滑动/叠加模型；算法侧依赖 C06 |
| OCR（C08） | — | — | — | **NOT_STARTED**（离线 OCR 依赖选型未评审；B09 已交付人工确认机制与数据模型） |
| 多标注渲染（C03） | `geometry/annotations.py`（命中/undo/redo/序列化） | 17 | Swift 已有 | 渲染本体是 Mac CoreGraphics 侧；几何合同已定 |

## 关键诚实边界（本队列钉死）

1. 质量提示永不判断证据价值（免责声明结构性恒附加）。
2. 近似重复永不等于相同证据（kind='near'/'exact' 结构分离）。
3. 隐私遮挡永不改原图；检测器候选永不自动应用（必须人工确认）。
4. 配准/差异检测不得自动判定工程质量（C06 未启动即在合同中声明）。
5. EXIF 命名缺陷已修复并钉死（见 C02 提交 `a2b7c07`）。

## 性能与已知限制

- 质量检测/感知哈希全为纯 Python + PIL；10MP 照片实测 ~0.3s/张
  （本机参考值，非 iPhone 值）。生产侧由 Mac 用 vImage/Metal 加速。
- 布局引擎零依赖（字体测量由调用方注入，跨端字节合同见 TM88 向量）。
- C06/C08 为依赖选型未评审项——**不虚报**、不预先手写大件。
