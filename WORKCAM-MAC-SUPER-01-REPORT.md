# WorkCam Mac Super 01 · Alpha increment

2026-11-27（北京）。独立分支 `codex/workcam-mac-super-01`，[Draft PR #26](https://github.com/zhltzh/workcam-ios/pull/26)，代码 `5130483dcd79808196211569420b894ecaacf7b1`。Mac 基线 `33cc1ca`，Windows 固定热修 `2a53712a179ad8880f75cc72d1762fb5b14ec0a8`。#24、#25 当前可合并，Mac #20–#24 的五组本地合并树通过。未追踪 Windows 管理 PR #25 的开发头，未追加 Swift Packages，未合并 main。

## 已交付

- iOS 真实 Swift HTTP 切换到固定热修，恢复 15 秒注册请求时限。同钥重注册、失效 Token 与挑战通过。整条测试约 6 秒，不能当作单次注册延迟或公网性能。Windows 上一轮热修的独立 SQLite 延迟数据仍见其报告。
- 同步中心支持全量事件流或按项目、整改、照片、标注下载。每个端点与过滤流维护单独游标，校验排序与推进条件，快照和游标一次原子写入。失败的磁盘写入保留原游标。
- 下载的服务器快照独立展示。可从活跃服务器项目排队提交改名或归档，携带 expected_revision。同步尚不会将服务器快照自动合并进本地编辑实体。
- Outbox 保存尝试次数、进行中、重试、冲突、失败和已确认 ID。重复拒绝不作为成功确认，已通过实际服务器回执对拍。独立任务跳过冲突调度及冲突解决中心待补。

## 测试结果

| 领域 | 实测 |
|---|---|
| iOS Debug / Release | 最终两配置无签名构建通过。 |
| Swift 桌面 | Inbox 重开、错误分页拒绝、写入失败时保留游标通过。 |
| XCUITest | 四项整套运行通过，另一次独立运行新增同步中心测试通过。0 失败。既有合成工程、六种标注、整改、PDF 和项目搜索继续验证。 |
| Swift 真实 HTTP | 真实回环注册与挑战、Outbox 重放、分块取消续传和原图确认、整改冲突、项目改名与归档、逐页拉取及保存后重开均通过。新增测试真实点击 Backend Sync 并观察归档快照。 |
| PDF / ZIP | 已导出合成六份 PDF 和一份 ZIP。独立 unzip 核验通过，ZIP 内 9 条文件哈希全部通过。 |
| 物理 iPhone | 在线。首次签名缺本机描述文件，启用自动签名后构建及原位安装成功，未卸载或清空 App。启动被 iOS 以 Locked 拒绝。实际拍摄、App Attest、Secure Enclave 实证、离线时间/定位、LiDAR、弱网、重启、真机独立验签均为 DEVICE_PENDING。 |

运行日志和命令见 [docs/progress.md](docs/progress.md)。结果包 `/tmp/WorkCamInt02-UI-super01-final.xcresult` 与 `/tmp/WorkCamInt02-UI-super01-download.xcresult`。本机图像和合成产物位于 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/super01/`。同步中心截图为 `sync/2F94695E-31A1-4CA3-9A4F-8841275AD159.png`，PDF 和 ZIP 位于 `samples/`。未生成全部九屏截图。

## 80 项范围与下一步

[任务书](docs/management/mac/MAC-SUPER-01-QUEUE.md)与[机器可读状态](docs/management/mac/MAC-SUPER-01-STATUS.json)已入库。状态计数从 80 条任务记录计算。当前为 3 LIVE_HTTP_VERIFIED、3 SIMULATOR_VERIFIED、2 DESKTOP_VERIFIED、4 DEVICE_PENDING、23 IN_PROGRESS、45 NOT_STARTED，含历史继承条目。这是一轮 Alpha 同步增量，完整 Alpha 仍未验收。

优先继续本地与服务器项目映射、整改编辑及角色撤回/回切、冲突解决和真机测试，然后推进 B–I 尚未交付的任务。未接入 Apple App Attest、Core Location 生产数据或新证据格式。高级测量精度未经物理实验。

## 体验

模拟器打开 Projects 可查看本地业务，也可使用现有测试方案创建合成工程。配置固定 HTTPS 后端，或用模拟器回环测试启动器，在 Backend Sync 下载服务器快照并排队提交项目改名或归档。实体 iPhone 上已安装开发候选，解锁后打开 WorkCam 即可继续人工真机验收。当前产品能力依旧保持离线可用，本地编辑不要求注册。
