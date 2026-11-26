# TECH-MARATHON-02 · 第一批进度（2026-11-26 下午）

分支 `dev/windows-tm-geometry`（基于 `07c69af`）· HEAD `3104784`（已推远端）
Python 侧回归：**85 passed / 0 failed**（几何核心 50 + TM88 向量消费 35）

## Swift 工具链结论（如实记录）

- `Swift.Toolchain 6.4.3`（官方）winget 静默安装**两次失败**
  （MSI 0x80070643：第一次 C 盘 6.4GB 不够，第二次清理后仍失败且
  吃掉 3.5GB 临时空间）。残留已清理（C 盘恢复 3.87GB）。
- 判定：**BLOCKED**。按开发书纪律转 CROSS_PLATFORM_PENDING 模式——
  Windows 编写 Swift 参考实现 + 共享向量，Mac 编译执行；Windows 永不
  声称 Swift 侧通过。

## 本批交付

| 任务 | Commit | 内容 | 状态 |
|---|---|---|---|
| TM13/15/16/17/18/19/20/22 | `56d50c5` | Python 几何核心（纯数学无 numpy，Swift 可移植）：反投影/EXIF 方向/深度图映射/裁剪缩放、距离/角度/坡度/单位、LSQ+PCA+RANSAC 平面拟合（手写 Jacobi 特征分解）、鞋带面积/自交检测/3D 平面投影、体积模型（假设随结果）、TM22 质量与一阶误差传播（未校准=显式保守）、TM13 WGS84/ECEF/ENU/方位角/地理围栏/GeoJSON/KML | DESKTOP_VERIFIED，50 tests |
| TM88（几何部分） | `56d50c5` | 34 例黄金向量（版本 geometry-vectors/1.0，输入+期望+容差+说明），Python 消费测试逐例重算比对 | DESKTOP_VERIFIED，35 tests |
| TM56/61 | `3104784` | WorkCamCore Swift Package：5 个纯 Foundation target，几何逐函数移植（含 Jacobi/RANSAC 种子差异的诚实说明） | CROSS_PLATFORM_PENDING |
| TM57 | `3104784` | Evidence 协议模型 + canonical payload 字节合同构造器（换行拒绝规则）+ 未知版本 typed 拒绝 + 六层结果模型；**不自行实现密码学**（CryptoKit 留 Mac） | CROSS_PLATFORM_PENDING |
| TM58 | `3104784` | 十维可信引擎 Swift 版（客户端策略：只解析展示绝不提升）+ 11 条规则测试（无绑定=NOT_ATTESTED、rotated_out 保数学失授权、外推时间/ GNSS 永不 VERIFIED、无总分、环境未声明=UNKNOWN） | CROSS_PLATFORM_PENDING |
| TM59 | `3104784` | 时间区间引擎（ClockSource 注入：Windows 合成时钟/Mac 真时钟）+ 重启→UNKNOWN + 过期降级 + 诚实注释进类型 | CROSS_PLATFORM_PENDING |
| TM60 | `3104784` | 定位质量（分带/陈旧/瞬移/海拔/手工永不评级，诚实注释在类型上） | CROSS_PLATFORM_PENDING |
| TM65 | `3104784` | Package.swift（含资源引用与版本漂移防护）+ 黄金向量 XCTest + 十维规则 XCTest + README（Mac 接入六步 + 限制清单） | CROSS_PLATFORM_PENDING |

## 下批（第一队列剩余）

- TM21：点云处理与降噪（体素降采样/统计离群/半径离群/法向量/PLY）
- TM23：测量开放格式（JSON/CSV/GeoJSON/PLY/SVG，含 JSON Schema）
- TM88 扩展：时间/定位/水印布局/同步领域的共享向量归并
- TM62/TM63/TM64：测量质量组件、标注几何、水印布局引擎的 Swift 侧
