# WORKCAM-MAC-KEY-01

## 首批交付

- [Draft PR #29](https://github.com/zhltzh/workcam-ios/pull/29)
- Mac 基线 `c6c6edaf2da1587455abd4d0f1ac06c7d685467b`
- Windows 固定参考 `2a53712a179ad8880f75cc72d1762fb5b14ec0a8`
- 固定代码 `b3c05d1dcdb03d93b25d6d4e0305bbe8bf2806bf`。其后的提交为文档。
- 点按对焦与测光、双指缩放、权限请求合并、媒体服务重置后的相机会话恢复已实现。相机仍采用固定竖屏方向。原图保存和证据格式沿用基线。

## Apple 技术验收矩阵

| 项目 | 状态 | 结果或待验收 |
| --- | --- | --- |
| M-K01 相机 | IMPLEMENTED / DEVICE_PENDING | Debug 和 Release 编译通过。需验证实际镜头、焦点、曝光、变焦、快拍、方向、中断、锁屏、权限恢复、飞行模式、强退、重启、低存储及原图 SHA-256。 |
| M-K02 设备可信 | DEVICE_PENDING | 本轮无 Secure Enclave 生命周期、App Attest、W018 验签或硬件来源实测。 |
| M-K03 时间与定位 | IN_PROGRESS / DEVICE_PENDING | 本轮未交付正式 Core Location 或服务端时间锚点联调，待验证离线定位、时间变化及证据关联。 |
| M-K04 ARKit 与 LiDAR | DEVICE_PENDING | 本轮无真机采样、校准或 CSV，三种模式精度待测。 |
| M-K05 产品体验 | SIMULATOR_VERIFIED（项目搜索） | iPhone 18 Pro、iOS 27.2 上运行实际项目搜索操作，XCUITest 两次各通过 1 项。其余界面待系统审查。 |
| M-K06 集成 | IN_PROGRESS | 应用 scheme 的 unsigned iOS Debug 和 Release 编译通过。签名 Alpha 候选、固定 Windows 组件接入与真实 HTTP 联调尚未交付。 |

## 测试和制品

- 首次以 workflow scheme 使用 generic iOS Simulator 构建，Xcode 无法解析目标。随后使用应用 scheme 构建 generic iOS，再以具体模拟器执行 UI 测试，两条路径均通过。命令见 `docs/progress.md`。
- Debug 和 Release 产物在本机 `/tmp/WorkCamKey01-Debug` 与 `/tmp/WorkCamKey01-Release`。均未签名。
- 模拟器结果为 `/tmp/WorkCamKey01-UI.xcresult` 和 `/tmp/WorkCamKey01-UI-record.xcresult`。
- 模拟器首页截图 `workcam-key01-simulator.png` 和项目搜索操作录屏 `workcam-key01-project-search.mov` 存放在本机 Codex 可视化目录。
- 物理 iPhone 枚举为离线。本轮无安装、真机相机操作、Apple 证明或测量数据。

## Windows 待接入

按固定 SHA 及独立测试向量验收冲突解决、双向同步、工程业务模型、图像处理、测量算法、报告生成、ZIP 导出、证据验证及对应 Swift Packages。本轮没有引入这些组件。

## 真机集中验收

设备解锁并在线后，原位安装签名候选。先覆盖飞行模式拍摄、对焦测光、缩放、快拍、强退、锁屏和重启，再核对原图 SHA-256、Secure Enclave、公钥连续性、独立验签、App Attest、真实时间定位、LiDAR 校准与导出。现有照片和证据需保留。
