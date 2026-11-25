# WorkCam Mac Night 03 研发报告

2026-11-25，北京时间

## 候选版本

以干净的本地集成提交 `08cc07b49a43c8f1c126f3217b2548b59c3d2ece` 为基线。单会话开发，旧工作树未删除。历史 4676a30 的两份真实 iPhone 导出、独立 OpenSSL 验签与一字节篡改拒绝继续有效。继承 97b0c84 的恢复和 Release 隔离，以及 08cc07b 的项目证据接线。

夜间代码提交 `dae446e1fedd570fc480fe1884a72cc040fb27ae`。已普通推送至 [codex/workcam-mac-night-03](https://github.com/zhltzh/workcam-ios/tree/codex/workcam-mac-night-03)。新 PR 尚未创建。历史 [PR #6](https://github.com/zhltzh/workcam-ios/pull/6)、[PR #9](https://github.com/zhltzh/workcam-ios/pull/9) 保留。

## 实际交付与剩余工作

| 任务 | 状态 | 本轮结果及缺口 |
|---|---|---|
| M014 | DESKTOP_VERIFIED / DEVICE_PENDING | 继承离线保存、SHA-256、签名及暂存恢复。现有恢复和异常回归通过。飞行模式、低存储、权限拒绝、强退与重启仍待真机。 |
| M015 | DESKTOP_VERIFIED / DEVICE_PENDING | 保留 Secure Enclave 与本设备 Keychain 逻辑；项目详情调用实际本地证据检查。远程硬件来源仍为 NOT_ATTESTED；App Attest 与服务端注册 Not Implemented。 |
| M018 | IMPLEMENTED / DEVICE_PENDING | 相机增加预览网格、大快门、设备时间、能力驱动的闪光灯及后摄镜头切换、最近成片预览、失败反馈与 Debug 测量入口。实时定位、联网状态、取景器快记和完整方向适配待完成。 |
| M019 | IMPLEMENTED / DEVICE_PENDING | 照片详情可选五种样式、四角位置，持久化可用字段开关，生成并预览独立 JPEG。保留设备时间和衍生图标签。W011 完整字段、Logo、背景采样和 1px 对拍尚未实现或验证。 |
| M020 | IMPLEMENTED / DEVICE_PENDING | 增加项目名搜索、归档和恢复、证据详情。旧项目兼容，归档只改元数据。照片搜索、标签、批量选择与导出待完成。 |
| M021 | DEVICE_PENDING | 继承单编号标记、备注与独立 sidecar。多种标注、撤销重做及整改闭环 Not Implemented。 |
| M022 | IMPLEMENTED / DEVICE_PENDING | Site Photo PDF 增加 A4/US Letter 与系统预览。Inspection/Evidence 版式、实际报告样例、ZIP 和复杂分页验收待完成。 |
| M016 | DESKTOP_VERIFIED / DEVICE_PENDING | 相机增加 Debug 实验入口。三种既有模式的桌面几何、归档与迁移通过。未采集真机 CSV 或精度数据。 |
| M023 | DESKTOP_VERIFIED / BLOCKED | 独立 Mock outbox 通过。真实身份认证、网络上传及业务同步 Not Implemented。 |
| M024 | DESKTOP_VERIFIED / DEVICE_PENDING | Debug、Release、独立回归通过。对产物符号表定向检查，Debug 有 LiDAR/篡改工具匹配，Release 无匹配。真机生命周期与性能待验。 |
| M017 / M025 | SIMULATOR_VERIFIED / DEVICE_PENDING | 独立夜间候选构建、模拟器安装与首页启动通过。核心业务交互及实体 iPhone 未执行。 |

## 可复核记录

- 核心代码修改在 `Core/Camera/CameraController.swift`、`Core/Storage/CaptureStore.swift`、`Features/Camera/CameraView.swift`。
- iOS Debug、Release 无签名以及 Debug 模拟器构建通过。签名 Debug 也通过，产物 `/tmp/WorkCamNight03-Signed/Build/Products/Debug-iphoneos/WorkCam.app`。
- 证据回归 7 组、LiDAR 回归 3 组、Mock 队列和旧项目归档兼容夹具通过。实际命令见 [研发进度](docs/progress.md)。
- iPhone 18 Pro 模拟器安装、启动和首页截图通过。截图位于 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/workcam-night03-home.png`。物理 iPhone 列为离线，未安装、未生成新真机证据。
- Windows 已获取 W011 规范与 10 例几何向量，连接器可见 B02 W014、W015、W016、W017、W021、W022、W026 等分支。依然缺已验证部署的固定 SHA、测试地址和 iOS 联调结果。

## 11 月 26 日真机集中验收

1. 用数据线连接并解锁 iPhone，按系统提示信任设备。由 00 原位安装签名候选，保留现有 App 容器和密钥。
2. 开启飞行模式并关闭 Wi-Fi。创建项目，试用可用镜头和闪光灯，拍摄两张无个人信息的测试照片。检查保存反馈、最近照片、原图哈希和本机签名状态。
3. 强退重开、锁屏解锁、重启首次解锁后重验同一批记录。再拍一张并比对公钥指纹。相机权限拒绝后恢复。低存储故障使用隔离测试环境。
4. 生成水印并核对原图哈希。预览两种纸张的 PDF，归档及恢复项目，导出证据。保存脱敏截图。
5. 用卷尺建立已知基准，分别测试三种 Debug LiDAR 模式、记录失败并导出 CSV。按模式单独分析。
