# WorkCam Windows → Mac 交接文档（Codex 可直接执行）

日期：2026-11-21（WC-W-TECH-004 封版更新至 Wave 4） · Windows 侧负责人签署 · 适用于 WC-W-TECH-003-R1 后的联调

本文件是 Mac 端开始任何联调前的**唯一入口**。按顺序执行；所有命令已
在 Windows 侧实际运行过，Windows 侧全量 **358 passed / 0 failed**
（提交见下）。本文件不含任何 iPhone 真机结论——真机数据由 Mac 产出。

Windows 18 项技术攻坚**阶段封版**：不再主动扩张技术范围；后续 Windows
工作以 Mac 真机发现的问题为优先（联调待命状态）。

---

## 0. 基线：必须使用的分支与提交

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/zhltzh/workcam-ios |
| 先合并 PR #8 | `dev/windows-tech-lab` → `main`（Wave 0+1，head `5a57c6a`） |
| 再合并 PR #10 | `dev/windows-tech-lab-wave2` → base `dev/windows-tech-lab`（Wave 2+3+4 全部包含） |
| 交接基线 commit | **`4ee8620`**（Wave 4 + 最终总报告；`git log origin/dev/windows-tech-lab-wave2 -1`） |
| Mac 分支 | 在你自己的 `dev/mac-*` 分支工作；**不得**提交到上述 Windows 分支 |

依赖关系（已验证 `merge-base --is-ancestor`）：PR #10 完整包含 PR #8，
合并顺序必须 **#8 先、#10 后**；#10 相对 #8 的 diff 为纯增量（GitHub
mergeable=True/clean，无冲突）。PR #7 与 PR #10 **零文件交集**。

## 1. T07/T08：JSON Schema 与测试向量（先对拍，再写代码）

文件（全部在 `test-vectors/tech-lab/`）：

| 文件 | 内容 | 你要做的 |
|---|---|---|
| `t07-calibration-vectors.json` | 4 例：2 例正常拟合（`expected_M`/`expected_t`/probe，1e-9）+ 共线拒绝 + 4 点残差拒绝 | 用 simd/自写实现拟合，与期望值逐项对拍；拒绝例必须 throw |
| `t08-projection-vectors.json` | `cases` 6 例投影 + **`arkit_conversion` 3 例完整转换链**（原始 ARKit 输入 → 期望协议位姿 1e-12 → 探测点投影 `expected_uv` 1e-6） | 先实现转换，再实现投影；`behind_camera_rejected` 必须抛错 |
| `record-schemas.json` | ARFrameRecord / DrawingSheet 的 JSON Schema | 生成 Codable 结构体；**`units` 字段必填**（`pixels`/`mm_35mm_eq`）——不许猜 |

坐标系合同（已钉死，勿自定）：
- 本协议 **+z 为相机前方**；ARKit 原生相机看向 -z
- 转换公式：`R_p = M180x @ R_a^T`，`t_p = -R_p @ t_a`，四元数闭式
  `q_p = (x=w_a, y=z_a, z=-y_a, w=x_a)`（M180x = diag(1,-1,-1)）
- `arkit_identity_looks_minus_z` 例是语义锚：ARKit 单位朝向 + 世界点
  (0,0,-4) → 转换后投影必须正好居中 (1920, 1080)
- Swift 实现建议：`simd_quatf` 取 ARKit transform → 按闭式换分量 →
  存入 ARFrameRecord；一切以向量文件为准，**与向量不符即实现有 bug**

## 2. T12：后端启动与客户端联调

Windows 侧启动（Windows 上运行；Mac 联调时指向同一台机器）：

```powershell
# Windows 后端（注意 opt-in 变量——实验上传端点默认禁用）
$env:WORKCAM_UPLOAD_EXPERIMENTAL = "1"
powershell -ExecutionPolicy Bypass -File tools\start_backend.ps1
```

Mac 侧客户端（纯 Python 标准库，任意机器可跑）：

```bash
python3 tools/tech-lab/validation/t12_upload_client.py <任意文件> http://<windows-ip>:8000
```

客户端自动执行完整协议：注册 → 乱序上传 → 故意丢块 → 断点续传 →
重复块（`duplicate_acked`）→ complete 整包 SHA-256 验证。退出码 0 =
服务器回执哈希与本地一致。

端点合同（全部在 `/upload/experimental` 前缀下，生产环境硬禁用）：
`POST /batches` · `PUT /batches/{id}/chunks/{index}` ·
`GET /batches/{id}` · `POST /batches/{id}/complete` ·
`GET /batches/{id}/content`（字节级取回）。语义细节见
`backend/techlab/upload_protocol.py` docstring（协议模型即合同）。

## 3. T13/T18：证据包验证流程

离线验签（无服务器、无网络，双平台同命令）：

```bash
python3 tools/tech-lab/validation/evidence_verifier.py <package_dir> --json report.json
```

状态语义：`PASS` / `PASS_UNREGISTERED_KEY`（数学过、身份未知）/ 
`CANNOT_VERIFY`（绝不显示为通过）/ `FAIL`。退出码 0=全过。
锚点回执验证：加 `--anchor-pubkey server_key.pem --anchor-key-id <id>`。

证据包结构（你构造包时遵守）：`package.json` + `photos/` +
`manifests/` + `derivatives/`（可选）+ `receipts/`（可选）。
字段合同见 `record-schemas.json` 与 `backend/techlab/evidence_package.py`。
构建器：`build_package()`（幂等；重复打包需 `force=True`）。

## 4. 八阶段业务链路验收标准

Windows 已用 SYNTHETIC 数据一次跑通（`backend/tests/test_techlab/test_full_chain.py`，
可直接读作脚本）。Mac 真机复现时**逐阶段对照**：

| # | 阶段 | 通过标准 |
|---|---|---|
| 1 | 拍照输入 | 原图字节 + T02 定位（source=gps）+ 签名清单（P-256/DER） |
| 2 | 原图封存 | 磁盘字节 SHA-256 == 账本 content_hash（`verify_original_bytes`） |
| 3 | 水印衍生 | 衍生图哈希 ≠ 原图哈希；原图封存后字节不变（重读验证） |
| 4 | 图纸关联 | 3 点校准 + 第 4 验证点残差 ≤ 0.05 m；GPS 定位佐证 pin |
| 5 | 证据包 | `verify_package()` → `all_passed`；衍生图回链无 problem |
| 6 | 流式导出 | zip 可解、`testzip()==None`；文件名字节与源一致 |
| 7 | 离线验证 | evidence_verifier 退出码 0，JSON `overall==true` |
| 8 | 上传恢复 | ≥3 分块；传 2 块→"断网"→重启后端→`GET /batches/{id}` 取缺失表→补传→重复块 `duplicate_acked`→complete 哈希==本地→`GET content` 字节级一致 |

第 8 阶段真机复现建议直接调 `t12_upload_client.py`，它本身就是 8 步
协议的参考实现。

## 5. 真机测试必须收集的原始数据

每项实验回填 `docs/tech-lab/experiments/<id>-results.md`（模板在
`acceptance-matrix.md`），原始数据入 `tools/tech-lab/datasets/`：

| 项 | 必须收集 |
|---|---|
| T01 SE 密钥 | 卸载/重装/清 Keychain 前后的 publicKeyID；G1-G8 每步后端 /verify 响应 JSON 原文 |
| T04 LiDAR | 按 `t04-checklist.csv` 的 132 条唯一 test_id 执行；CSV 原样导出（含失败行，不许删）；每条带 elapsed_s、failure 原因 |
| T07/T08 向量对拍 | Swift 实现的 M/t 与 (u,v) 输出 vs 向量期望值的逐例 diff（全过截图/日志） |
| T08 真机投影 | 一帧 ARKit transform + intrinsics 原始值 + 屏幕标注的 (u,v)，离线用 Python 重算对比 |
| 链路复现 | 8 阶段每阶段的产物路径 + 最终 `GET content` 字节比对结果 |
| T15 模型评测 | 候选 Core ML 模型 + 真实裂缝照片测试集；与 Windows 经典基线（`techlab/defect_detection.py`，合成 recall 0.76）同指标对比的完整数字 |
| T16 识别实测 | on-device 转写输出 + 现场音频样本（噪声/口音/术语各至少一组）；输入 Windows 溯源协议跑全链审计 |
| T17 真实照片对 | 同机位不同时期照片对（含视角变化样本）；vImage 配准结果回传，对照 `techlab/change_detection.py` 行为合同 |

红线：**不得**把桌面数字当 iPhone 数字；**不得**删除失败样本；
**不得**在无真机结果时把状态改出 DEVICE_PENDING。

## 6. 尚未解决的问题（封版清单，按归属划分）

### 6a. Windows 可解决（联调中发现问题时随时修复）

1. T12 实验端点鉴权层（若联调需要跨网络部署——当前 opt-in +
   生产否决双门下仅限可信网络，已按第 5 节复验）
2. PR #7（W005）合并前 main 上少 18 项 W005 回归测试（techlab 侧
   不受影响；合并后总数自然归一）
3. 文档/协议措辞微调、Mac 对拍发现的向量偏差修正

### 6b. 必须依赖 iPhone / Mac（Windows 无法代做）

1. **零真机数据**：T01 SE 密钥生命周期、T04 LiDAR 132 条清单全部
   实测、T08 ARKit 位姿序列化、T09 iOS 编码渲染、T10 CoreGraphics
   渲染一致性、T11 iPhone SQLite 数字重测、T18 macOS 一次冒烟、
   八阶段链路真机复现
2. **Wave 4 真机项**：T15 Core ML 模型选型 + 真实裂缝照片评测
   （Windows 只给了合成基准与经典基线，模型必须击败基线才算有效）；
   T16 on-device 语音识别实测（工地噪声/口音/术语）；T17 vImage/
   Accelerate 配准 + 真实照片对（视角变化）
3. **产品决策类**：T14 C2PA 证书账户注册（外部账号依赖）

### 6c. 如实声明的边界（非问题，写入协议/报告，不可"修复"掉）

1. T14 桥接**非** C2PA 标准合规（十项差异表：`t14-c2pa-diff.md`）
2. T07 图纸修订自动迁移（文本锚点）是 v2 研究项，未承诺
3. DOCX/XLSX 显示尺寸固定 320×240（真 JPEG 尺寸解析留给 iOS ImageIO）
4. Office 实测仅到 Office 2007（本机版本），365/WPS 未验证不外推
5. T12 客户端重试节奏（退避）未规定——协议只锁语义
6. T17 仅平移配准（视角/尺度是**已声明**盲区，随每份报告输出）
7. Wave 4 全部为合成数据（SYNTHETIC 标注），桌面数字不外推 iPhone

## 7. T12 端点安全（第 5 节要求，封版复验）

实验上传端点 `/upload/experimental` 的暴露控制（全部有测试钉死，
封版复验通过：`test_opt_in_disabled_by_default` /
`test_production_veto_beats_opt_in` 均绿）：

1. **双重门**：默认禁用（未设 `WORKCAM_UPLOAD_EXPERIMENTAL=1` → 503）；
   即使设了 opt-in，`WORKCAM_ENV=production` 仍硬否决（503）
2. 生产部署**忘记设任何变量**时端点保持关闭（默认拒绝）
3. 部署纪律：`start_backend.ps1` 不设置 opt-in（联调时显式
   `$env:WORKCAM_UPLOAD_EXPERIMENTAL="1"` 临时开启）；面向公网时
   绑定 `--ListenHost 127.0.0.1`（默认）或用防火墙隔离——**该端点
   无鉴权，不得暴露公网**
4. 资源防线：块 4MiB / 整包 2GiB / 512 块 / 64 活跃批量 / 72h TTL
   清扫 / traversal id 十六进制守卫 / 并发 tmp 唯一化

## 8. 快速命令速查（Mac 侧）

```bash
git fetch origin
git checkout -b dev/mac-joint-debug origin/dev/windows-tech-lab-wave2
python3 -m pip install -r backend/requirements.txt   # 联调工具只需这组依赖
python3 tools/tech-lab/validation/gen_cross_vectors.py    # 重新生成向量（幂等，可核对）
# 对拍 T07:
#   读 test-vectors/tech-lab/t07-calibration-vectors.json，Swift 拟合 → diff
# 对拍 T08:
#   读 t08-projection-vectors.json 的 arkit_conversion + cases → diff
# T12 联调（后端先在 Windows 上跑，带 opt-in，见第 7 节）:
python3 tools/tech-lab/validation/t12_upload_client.py <file> http://<win-ip>:8000
# 证据包验证:
python3 tools/tech-lab/validation/evidence_verifier.py <pkg> --json r.json
```

Windows 侧状态：**联调待命**。Mac 真机发现问题请以 issue/PR 评论
形式提出，Windows 按问题优先响应，不主动扩张技术范围。
