# Mac Ready · 第一份冻结候选（M03-H09/H10）

**候选固定版本**：`dev/windows-tm-geometry` @ `e5da8e4`（含 WIN-HOTFIX-01~04 集成 `cd1219b`）
机器可读清单：`handoff/mac-ready/integration-manifest.json`（12 组件，每项含路径/依赖/验证状态/接入方式/限制）

## Mac 接入顺序（按依赖）

1. **克隆**：`git clone --branch dev/windows-tm-geometry <repo>`，`cd backend`
2. **后端起服务**：`bash bootstrap.sh`（macOS bootstrap，跑一次回报即可）→
   `python scripts/db_admin.py init` → `uvicorn app.main:app`
3. **验证合同**：`python scripts/win_hotfix_live.py .`（真 HTTP 全链）
4. **Swift 包**：`swift test --package-path ../swift/WorkCamCore`
   （黄金向量资源已在包内；版本不符即 FAIL）
5. **十项真机验收**：`docs/mac/TM55-HANDOFF.md` 第 F 节

## 12 组件一览

| # | 组件 | 状态 |
|---|---|---|
| 01 | EXIF 1..8 + 极端布局黄金向量（修复版） | DESKTOP_VERIFIED |
| 02 | 工程模板 + 检查清单 + 动态表单 | DESKTOP_VERIFIED |
| 03 | WorkCamCore Swift 包（Geometry/Models/Evidence/Time/Location/Sync） | CROSS_PLATFORM_PENDING |
| 04 | 流式 ZIP64 接口 + 参考实现 | DESKTOP_VERIFIED |
| 05 | 六种专业报告数据模型（A4+Letter，Page X of Y） | DESKTOP_VERIFIED |
| 06 | 开放证据包 V2 | DESKTOP_VERIFIED |
| 07 | 离线网页验证器（单文件 HTML） | DESKTOP_VERIFIED（mirror） |
| 08 | API Client 契约（Integration 02 对拍完成） | DESKTOP_VERIFIED |
| 09 | 双向同步协议（上行 batch + 下行 events 游标） | DESKTOP_VERIFIED |
| 10 | 时间与定位组件（Mac Protocol02 数值已对拍 1.015 一致） | DESKTOP_VERIFIED |
| 11 | 工程测量算法（几何/点云/大地测量） | DESKTOP_VERIFIED |
| 12 | 真机验收清单 | DEVICE_PENDING（Mac 执行） |

## 诚实边界（本候选永不越线）

- Swift 未在 Windows 编译（4 次工具链安装失败，如实记录）
- 十维引擎是隔离候选，未经评审不接生产 verify 路由
- 全部 device 项未宣称；App Attest 合成向量 ≠ Apple 材料
- 推理时间非认证拍摄时间；GNSS 非不可伪造证明
