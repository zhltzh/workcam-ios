# WorkCam Mac Night 05

2026-11-26 北京时间。基线 `57076c9`，分支 `codex/workcam-mac-night-05`，代码提交 `f6774efde9577ec4e9fa03dd38424c210f07b448`。

## 已交付

| 模块 | 状态 | 结果 |
|---|---|---|
| 项目证据事务 | DESKTOP_VERIFIED / SIMULATOR_VERIFIED | 项目归属、原图和证据元数据进同一暂存目录，通过一次目录重命名发布。旧引用继续读取，孤立引用不会显示为照片，孤立证据保留。恢复不会覆盖或删除原图。 |
| 多标注 | DESKTOP_VERIFIED / SIMULATOR_VERIFIED | 六种形状，颜色、线宽、选择与重画、属性编辑、删除和会话内撤销重做。标注集合原子保存，旧单标注可读。画布、水印与 PDF 共享标注渲染。 |
| 整改 | IMPLEMENTED / SIMULATOR_VERIFIED | 参照 W013 的四级严重度、顺序状态和 Before/Progress/After。项目内验证照片关联，操作历史记录状态及业务快照。已关闭问题只读。 |
| PDF / ZIP | DESKTOP_VERIFIED / SIMULATOR_VERIFIED | Inspection 与 Evidence 加入整改、照片对比、标注和证据检查结果。ZIP 离线包含原图、衍生图、标注、问题记录、两份 PDF、证据材料、manifest 和 SHA256SUMS。 |

## 实际验收

- Debug、Release 无签名 iOS 构建通过。模拟器 XCUITest 构建及 **2 项真实点击测试通过，0 失败**。点击覆盖项目创建、搜索、归档并查看归档、照片查看、添加并保存标注、生成水印和进入 Quick Look PDF。测试照片为合成输入。测试代码在 `Tests/UI/WorkflowTests.swift`。
- 证据桌面回归 10 组通过，包含注入原图写入失败、部分写入与签名恢复、冲突和幂等。桌面项目夹具通过旧项目解码、旧标注读取、新集合重读、空集合覆盖旧标注及原图不变。
- 隔离模拟器业务夹具通过注入签名失败、项目不存在、崩溃暂存、重复恢复、孤立引用与证据、六形状重读、EXIF 6 / 横竖图解码、整改历史、六份 PDF 打开及原图一致性。模拟器签名行为仅供测试。
- 独立 `unzip -t` 验证 ZIP 可打开，解包后 `shasum -a 256 -c SHA256SUMS` 验证 9 条文件哈希全部通过。对比现场真实性及远程硬件来源仍为 NOT_ATTESTED。
- 失败记录包括第一次模拟器签名假设错误、系统搜索遮挡和 Quick Look 按钮定位错误。修复后第六轮两项通过，真实结果 `/tmp/WorkCamNight05-UI-6.xcresult`。截图保存在 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/night05/`。通过截图为 `pdf-preview-pass.png`、`project-search-pass.png`，失败截图在 `failure-1` 至 `failure-4` 及 `run-5`。
- 交付目录同上，含 `Synthetic Delivery.zip`、六份合成 PDF 和水印样例。命令见 [docs/progress.md](docs/progress.md)。

## 真机与剩余事项

**DEVICE_PENDING**。交付前再次枚举时实体 iPhone 仍离线。本轮没有安装，也没有采集新的真实证据。设备在线后原位安装，再集中进行飞行模式拍摄、哈希与验签、导出、强退、锁屏解锁、重启与公钥指纹检查，以及水印、PDF 分享和 LiDAR 基础测量。

坐标覆盖正常横图和 EXIF 6 的竖图，其他 EXIF 镜像方向、边缘标注和更多比例仍待实测。真实异常包括物理断电、存储耗尽仍待真机。XCUITest 当前没有遍历六种工具及整改状态机的全部交互。ZIP 使用不压缩 ZIP32，内存预算约 128 MiB。Windows 远程同步、完整跨端字段对拍及后台签名身份仍待联调。

## 审查顺序

[历史 PR #9](https://github.com/zhltzh/workcam-ios/pull/9) → [Night 03 草稿 #20](https://github.com/zhltzh/workcam-ios/pull/20) → [Night 04 草稿 #21](https://github.com/zhltzh/workcam-ios/pull/21) → [Night 05 草稿 #22](https://github.com/zhltzh/workcam-ios/pull/22)。各夜以直接前序分支为 PR 基线。未合并 main。
