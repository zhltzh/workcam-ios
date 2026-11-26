# WorkCam Mac Integration 01

2026-11-26（北京）。代码提交 `48d963917ab692d235e3d7377abf6f52d9a90c2c`，独立分支 `codex/workcam-mac-integration-01`。

## PR 审计

| 草稿 | 基线 | 分支头 | 当前检查 |
|---|---|---|---|
| [#20](https://github.com/zhltzh/workcam-ios/pull/20) | dev/mac-tech-lab `4676a30` | Night 03 `008dfea` | GitHub mergeable=true，本地 0 左侧 / 9 右侧独有提交 |
| [#21](https://github.com/zhltzh/workcam-ios/pull/21) | Night 03 `008dfea` | Night 04 `57076c9` | mergeable=true，0 / 1 |
| [#22](https://github.com/zhltzh/workcam-ios/pull/22) | Night 04 `57076c9` | Night 05 `ed7dd7b` | mergeable=true，0 / 2 |
| [#23](https://github.com/zhltzh/workcam-ios/pull/23) | Night 05 | Integration 01 | 新建 Draft，审查本轮增量 |

前三组的本地 `git merge-tree --write-tree` 均成功。当前没有可重现的内容冲突。缺少当时 API 快照，无法确定历史 false 的原因。已有 PR 基线、历史证据与主分支保持原状。未强推、未合并。

## 修复与测试

- 证据导出和 PDF 状态检查现在核对目录与记录 ID、原图哈希及独立 DER。桌面回归验证缺失或错误 DER 会拒绝导出，原有正确导出继续通过。
- 修复 UTF-8 CRLF 校验缺口，继续保留现有 v1.1 生产格式。ZIP 将本地头、中央目录和尾记录纳入 128 MiB 成品上限，并在读取大文件前检查容量。桌面覆盖恰好边界及超限拒绝。成品限额不代表峰值内存，真机压力实测待做。
- iOS 无签名 Debug、Release 均通过。首次编译遇到宿主 SwiftUI 宏进程资源耗尽，降低并发后通过。模拟器 arm64 XCUITest 最终 **3 项通过，0 失败**，新增六种工具拖放与整改状态重读。证据桌面 10 组、旧项目/标注兼容和 ZIP 边界测试通过。
- XCUITest 结果 `/tmp/WorkCamInt01-UI-2.xcresult`，截图在 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/integration-01/`。构建和测试命令见 [研发进度](docs/progress.md)。

## 固定 Windows 交接与真实 HTTP

使用后端 `2b535a047e5f9e5c9c5091da949a591442b4ab61` 和索引 `07c69af11905fa5f0a94705181561ba50050e8c1`。

- **TM02** 用 Swift CryptoKit 验证 96 份 Windows DER 与精确载荷哈希，对拍当前 App 支持的 56 组 canonical 字节，拒绝 4 组非法输入。Windows 生成的原 XCTest 使用了不适用的 CryptoKit 参数标签和签名类型，因此保留固定 JSON 数据并以可执行 Swift runner 复验。1.0、结构化定位和超出 App 生产上限的序号只验证固定载荷，未接入当前 App 的生成器。
- **TM01 / TM10 / TM12 / TM32 / W018 / TM34 / TM35** 等固定后端定向回归合计 **120 通过**。TM32 Swift 只读镜像另与 Python 64 种事实组合逐一一致。TM01 完整 Swift Schema、TM10 与 TM12 端侧接线待完成。
- **W018** 将两份历史真机导出在隔离临时目录包装成 Windows 支持的目录格式，独立 CLI 退出码为 0。原始导出保持原样，未上传、未提交，结论仅覆盖数学验签和绑定。
- **真实 HTTP** 用固定代码在本机回环启动后端。执行设备注册与 Token、挑战签名、项目批次重试、照片登记、受服务器重启打断的分块上传、续传、完整性校验、整改更新及过期 revision 冲突。客户端是可复跑 Python 测试，使用合成字节和测试密钥。现有 iOS Mock 和 Swift 客户端接线不计入这次 HTTP 成功。
- 配置的包源缺少锁文件指定的 `annotated-doc==0.0.7`，系统 Python 3.9 也无法加载后端类型声明。实际使用隔离 Python 3.12 和兼容依赖；后端代码保持固定。没有验证 Windows 依赖锁的逐包等价。

## 待完成

- **DEVICE_PENDING**。实体 iPhone 离线，未原位安装。飞行模式、Secure Enclave、App Attest、强退及重启、时钟、定位、LiDAR、弱网、真机峰值内存仍待集中验收。
- iOS Token 保存、TM35 上传和 TM34 冲突接入、整改真实客户端同步仍待开发。真实 HTTP 当前覆盖桌面客户端和固定后端。十维引擎及新协议保持隔离。
- 旧项目与多标注继续通过桌面回归。更多 EXIF 方向、边缘坐标和大批量导出设备压力尚未完成。
