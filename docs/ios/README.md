# WorkCam

WC-001 / Task 001 原生 iPhone 工程初始化。所有产品界面使用英文。

## 技术与范围
Swift 6 + SwiftUI App 生命周期；系统 AVFoundation、CryptoKit、Security / Keychain、Core Location，无第三方依赖。最低 iOS 17.0；现有 Xcode 27.2 提供 iOS 27.2 SDK，可构建该部署目标。旧版系统兼容性尚未实机验证。

Task 002 已加入后置相机实时预览、JPEG 单张拍摄及 App 内本地保存；仅进入相机页面时申请相机权限。处理拒绝授权、前后台、会话中断和保存错误。Signing / Network 仍为 Not Implemented，无联网、定位或签名，不存在 Verified 结论。照片保存在 Application Support/Captures，启用完整文件保护并排除备份；尚无相册、导出和删除入口，卸载会丢失照片。界面暂限定竖屏。

## 编译运行
使用 Xcode 打开 WorkCam.xcodeproj，选择 WorkCam scheme 和 iPhone 设备或已安装的模拟器。真机需在 Signing & Capabilities 中选择自己的 Development Team，连接并信任 iPhone、启用 Developer Mode。当前未配置团队。

开发占位 Bundle ID 为 com.example.workcam.dev。正式发布前在目标 Build Settings 的 PRODUCT_BUNDLE_IDENTIFIER 中替换为组织拥有的唯一反向域名标识，在开发者账号注册对应 App ID，重新配置团队、证书及 provisioning profile，并检查 Debug / Release 最终值。不要提交私钥或证书。

Debug / Release 分别使用 Configurations 中的 xcconfig。构建记录及环境见 docs/ios/progress.md。

## 目录
App 为入口；Features 为界面；Core 为系统服务边界；Models 为共享模型；Resources 为资源；Tests 为未来测试；docs 为设计文档。空模块通过 .gitkeep 保留，没有预先创建多余服务抽象。

后续范围、风险见 docs/ios/architecture.md 与 docs/ios/threat-model.md。本次仅含最小拍照流程，不包含完整拍照、Secure Enclave 签名、App Attest 服务端、同步、ARKit / LiDAR 或支付。
