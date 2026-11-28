# WORKCAM-MAC-KEY-02

- [Draft PR #30](https://github.com/zhltzh/workcam-ios/pull/30)
- GitHub 基线 `b3c05d1dcdb03d93b25d6d4e0305bbe8bf2806bf`
- 固定代码 `bfc11dcd15ee6bcf16d33dcece77d99601636348`

## 实现与测试

- 相机区分镜头支持的点按对焦与测光能力，并向用户显示请求结果。界面反馈表示请求被提交，不代表焦点已锁定。对焦指示框、缩放基准、EXIF 和硬件中断恢复仍待实现或验收。
- Core Location 在用户点击 Check Location 后请求前台授权和单次定位。界面显示精度授权、水平误差和采样时间，并区分拒绝、失败及陈旧定位。位置坐标不显示或保存。拍摄记录关联和 Windows 时间锚点接入仍待完成。原图与历史签名格式保持基线。
- 新增相机 XCUITest，覆盖实际进入相机、切换网格、无物理相机时的快门禁用和定位初始状态。iPhone 18 Pro 模拟器执行 1 项、失败 0 项。本机结果 `/tmp/WorkCamKey02-UI-final.xcresult`。首次编译发现 Core Location 委托隔离错误，修正后测试通过。
- Xcode 27.2 的未签名 generic iOS Debug、Release 构建均通过。准确命令保存在本地 `docs/progress.md`。该文件含待脱敏的历史日志，未发布该文件的变更。

## 待交付与证据边界

- DEVICE_PENDING：实际镜头和 EXIF、飞行模式拍摄、强退重启、中断恢复、定位实测、原图 SHA-256 与 W018 验签、Secure Enclave 与 Apple App Attest、ARKit / LiDAR 采样。
- 本轮继承 LiDAR Lab 的 Tracking State、三种模式及 CSV。照片关联、校准实验与真实数据未交付。
- 等待固定 Windows 交接 SHA 与时间锚点、App Attest 验证及 W018 契约。未替换通用算法或生产签名格式。
- PR #30 采用 PR #29 的分支作为审查目标。连接器初次返回 `mergeable=false`。本地 Git 缺少固定远端提交对象，未能执行 PR #29 的 merge-tree。

## 物理 iPhone

iPhone 17 Pro Max 在线。签名 Debug 构建通过，原位安装和启动命令成功，未执行卸载或数据清理。现场拍摄、授权操作及硬件验收仍为 DEVICE_PENDING。未从手机提取照片或设备标识。
