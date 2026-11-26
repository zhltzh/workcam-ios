# WorkCam Mac Integration 02

2026-11-27（北京）。基线 `7940fc8680b37088108947cf7531ab4ff1a4a4ac`。代码 `53440feaf734c9ab21e29ddde9666fb8b5ef77cc`。独立分支 `codex/workcam-mac-integration-02`，[Draft PR #24](https://github.com/zhltzh/workcam-ios/pull/24) 基于 #23。GitHub 当前报告 #20–#23 都是开放草稿且 mergeable=true。逐个核对 base/head 为相邻前序分支。历史 #23 异常未复现，未改基线、未强推、未合并 main。

## iOS 实现

- Backend Sync 允许手动选择项目、填写服务地址、排队和发布。Keychain 按端点保存 Token，仅本设备解锁时可用。注册后执行 P-256 挑战，失效 Token 清理后可同钥重新注册。拒绝重定向，非回环地址要求 HTTPS。
- 只读原图经过 SHA-256 后分块上传。重试时向服务端重开会话，跳过已收到块，最终检查绑定和哈希。取消或请求失败保留未确认操作。客户端界面只显示队列或服务端确认，证据可信状态保持原格式与边界。
- 持久化 Outbox 记录请求快照、操作 ID、依赖、已确认 ID 和服务端 ID。支持首次项目/照片发布、整改创建、正向状态流转及照片角色关联。版本冲突保留在队列并显示服务器详情。现有项目改名、已发布问题内容编辑、关联删除/回切、下行拉取、跨设备并发恢复仍待做。
- TM01 的隔离结构校验、TM10 区间与重启拒绝、TM12 定位质量和风险信号已移植并执行桌面 Swift 夹具。尚未接入采集或生产协议。

## 验证边界

| 范围 | 实际结果 |
|---|---|
| Swift 客户端真实 HTTP | iPhone 18 Pro 模拟器内启动 App，由 XCUITest 驱动真实 URLSession。固定后端注册、Token Keychain 重用、注入失效 Token 后恢复、挑战签名、Outbox 重读、项目/照片重放、第一块后中断及跳块续传、最终完整性、整改角色与版本冲突通过。确认源文件未变。一次测试覆盖整链。结果 `/tmp/WorkCamInt02-UI-6.xcresult`。 |
| 模拟器与构建 | 上述 iOS XCUITest 通过 1 项，0 失败，使用合成字节。独立 iOS Debug 与 Release 无签名构建均成功。截图保存在本机 `/Users/zhaohui/.codex/visualizations/2026/11/24/01a20f8b-b70f-7793-b626-b9e0f03bf6b2/integration-02/65BF7CF7-2079-4730-A6F0-949EFA02CDB1.png`。 |
| Python 桌面 HTTP / CLI | 两份历史真机照片经本机固定后端 `/verify` 与 W018 独立 CLI 双重验证，均通过哈希和数学签名。Python 仅用于此独立验证，不计作 Swift 端测试。原始历史包未修改。 |
| 物理 iPhone | **DEVICE_PENDING**。实体 iPhone 仍离线，未安装、未拍新照片。强退、重启、弱网、时间定位、LiDAR 和 Secure Enclave 硬件验收均待做。 |
| 真实 Apple 证明 | **DEVICE_PENDING**。未执行真实 App Attest 或 Apple 服务端验证。硬件来源仍为 NOT_ATTESTED。 |

固定后端同钥重复注册在持有 SQLite 写事务时调用独立审计写入，触发约 30 秒忙等待。客户端把注册超时单独设为 40 秒，普通请求仍为 15 秒。最终测试通过，固定后端未改动。取消测试通过的是第一块后合成取消及重新读取持久队列，尚未验证真实进程被杀或多设备竞争。

## 干净环境复现

固定后端 `2b535a047e5f9e5c9c5091da949a591442b4ab61`，Windows 索引沿用 `07c69af`。用 macOS arm64 CPython 3.12.16 和本分支锁文件。独立临时 venv 已验证逐包安装和 FastAPI 导入成功。

```sh
wc_fixed=$(mktemp -d)
git archive 2b535a047e5f9e5c9c5091da949a591442b4ab61 backend tools/evidence-verifier test-vectors | tar -x -C "$wc_fixed"
uv venv --python 3.12.16 "$wc_fixed/venv312"
uv pip sync --python "$wc_fixed/venv312/bin/python" Tests/Integration02/backend-macos-py312.lock.txt
bash Tests/Integration02/run-simulator.sh "$wc_fixed" clean-1
```

运行前启动 iPhone 18 Pro 模拟器，并确保本机回环 18765 未被占用。脚本使用临时数据库与合成注册参数。更多实际命令见 [研发进度](docs/progress.md)。
