# WorkCam Mac Night 04

2026-11-25 · 基线 `008dfea3b67cd8938c407b5aacf3a4b911d72c0e` · 分支 `codex/workcam-mac-night-04`

## 已交付

| 任务 | 状态 | 结果 |
|---|---|---|
| M014 / M024 | DESKTOP_VERIFIED | 写原图前持久化预期 SHA-256。若原图已落盘且匹配、签名尚未完成，可恢复为 unsigned。若已落盘的签名载荷验证通过，且预期哈希一致，可恢复尚未落盘的 DER。冲突记录和不匹配原图继续隔离。导出失败清理临时输出。 |
| M021 | IMPLEMENTED / SIMULATOR_VERIFIED | 编号标记、箭头、矩形、椭圆、文本和自由绘制共用渲染路径。支持当前编辑会话撤销重做，原有标记格式兼容。自动化闭环实际保存并重读的是箭头。 |
| M022 | IMPLEMENTED / SIMULATOR_VERIFIED | Site Photo、Inspection、Evidence 三种报告入口，各支持 A4 和 US Letter。证据版显示原图 SHA-256。模拟器生成六份样例，PDFKit 实际打开并核对页数和纸张尺寸。 |
| M025 | SIMULATOR_VERIFIED / DEVICE_PENDING | iPhone 18 Pro 模拟器运行隔离的合成闭环。创建项目 → 写入合成 JPEG → 证据关联和照片重读 → 标注、水印和六份 PDF → 证据导出。导出原图和存储原图均与输入逐字节相同。 |

## 构建与证据

- 无签名 iOS Debug、Release 和 Debug 模拟器构建均成功。Release 的定向符号扫描无 LiDAR、证据篡改工具或合成测试入口匹配。
- 证据桌面回归九组通过，含错误哈希拒绝、恢复后的原图不变、缺失 DER 重建、提交冲突和重复恢复。LiDAR 桌面回归三组通过。实际命令记录在 [docs/progress.md](docs/progress.md)。
- 模拟器使用 Debug 专用启动参数 `--night04-fixture`。测试覆盖业务路径和可打开的衍生文件。没有自动点击相机、标注手势、Quick Look 或系统分享界面，也没有用模拟器证明硬件签名。
- 六份合成 PDF、水印 JPEG 与运行结果截图保存在本机 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/night04/`。
- Xcode 列出物理 iPhone 离线。本轮未进行真机安装与验收。历史证据文件保持原样。

## 未完成

单张照片仍只保存一个标注。多标注、颜色线宽、整改问题与历史仍待实现。Inspection 版沿用照片和人工备注布局，尚未接入整改清单。报告 ZIP、完整交付清单及 Windows 真实联调未完成。端到端 UI 点击、摄像头、硬件签名、磁盘不足与真实强退窗口仍待实测。远程硬件来源仍为 NOT_ATTESTED。

## 真机集中验收

连接并解锁 iPhone 后原位安装，保留既有数据。飞行模式拍照并验证证据，完成强退、锁屏解锁和重启后复核。随后测试各类标注、水印和两种纸张的 PDF 预览及分享，最后导出同一原图并核对 SHA-256。
