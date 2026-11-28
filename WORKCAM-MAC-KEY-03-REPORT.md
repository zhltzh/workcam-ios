# WORKCAM-MAC-KEY-03

- [Draft PR #31](https://github.com/zhltzh/workcam-ios/pull/31)
- 基线 `bfc11dcd15ee6bcf16d33dcece77d99601636348`
- 固定代码和测试 `8e18c0d6c1559ac19212cf95ab5558d5a12b528c`

## 已实现

每次按快门冻结版本化 CaptureContext，记录设备报告的快门请求时间、定位授权、采样时间、坐标、精度、来源类别或缺失原因。有效样本来自用户主动请求的 Core Location 单次定位。检查样本新鲜度与系统时钟跳变。拍摄事务将快照写入证据记录的 unsignedCaptureContext。证据目录导出复制该记录，因此可能包含定位坐标。该字段独立于 v1.1 DER 签名载荷。旧记录可继续读取，原始 JPEG 保存使用原字节。

## 实际验证

- Xcode 27.2 未签名 Debug、Release 和签名 Debug 均构建通过。
- iPhone 18 Pro 模拟器相机回归 1 项通过，覆盖现有相机控件，不执行真实拍摄。
- macOS Swift 证据测试通过，覆盖两次提交的快照独立性、拒绝状态、模拟来源标记、原图字节、旧记录解码、固定签名向量与篡改拒绝。完整 Core Location 质量分支和实际网络定位仍待测试。
- iPhone 17 Pro Max 两次原位安装均未到达安装完成。首次设备锁定，解锁后开发镜像个性化失败（CoreDevice 12051）。未卸载或清理设备。

## 待验收

DEVICE_PENDING：原位拍摄、重开、弱网或飞行模式、实际照片 SHA-256、独立 W018、Secure Enclave 生命周期、ARKit/LiDAR 实测、相机中断恢复。App Attest 未联调，状态 NOT_ATTESTED。Windows 侧仍需稳定的服务端 Apple 证明契约和独立验证器。未完成本地 PR 堆栈 merge-tree 或 CI 重跑。
