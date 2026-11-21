# WorkCam Windows → Mac 交接文档（Codex 可直接执行）

日期：2026-11-21 · Windows 侧负责人签署 · 适用于 WC-W-TECH-003-R1 后的联调

本文件是 Mac 端开始任何联调前的**唯一入口**。按顺序执行；所有命令已
在 Windows 侧实际运行过，Windows 侧全量 **337 passed / 0 failed**
（提交见下）。本文件不含任何 iPhone 真机结论——真机数据由 Mac 产出。

---

## 0. 基线：必须使用的分支与提交

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/zhltzh/workcam-ios |
| 先合并 PR #8 | `dev/windows-tech-lab` → `main`（Wave 0+1，head `5a57c6a`） |
| 再合并 PR #10 | `dev/windows-tech-lab-wave2` → base `dev/windows-tech-lab`（Wave 2+3，R1/R2/W003/R1 全部包含） |
| 交接基线 commit | **`4bb0a43`**（W003 集成）→ 本轮 R1 追加提交（见 PR #10 最新 head） |
| Mac 分支 | 在你自己的 `dev/mac-*` 分支工作；**不得**提交到上述 Windows 分支 |

依赖关系：PR #10 完整包含 PR #8（已验证 `merge-base --is-ancestor`），
合并顺序必须 **#8 先、#10 后**，#10 的 diff 才是纯增量。

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

红线：**不得**把桌面数字当 iPhone 数字；**不得**删除失败样本；
**不得**在无真机结果时把状态改出 DEVICE_PENDING。

## 6. 尚未解决的限制（如实）

1. **零真机数据**：T01/T04 全部、T08 ARKit 序列化、链路真机复现均待 Mac
2. T12 无鉴权（实验端点；opt-in + 生产禁用双门，仍仅限可信网络）
3. T14 仅断言映射桥接，**非** C2PA 合规（差异表：`t14-c2pa-diff.md`）
4. T07 图纸修订自动迁移（文本锚点）是 v2 研究项，未承诺
5. DOCX/XLSX 显示尺寸固定 320×240（真 JPEG 尺寸解析留给 iOS ImageIO）
6. Office 实测仅到 Office 2007（本机版本），365/WPS 未验证
7. T12 客户端重试节奏（退避）未规定——协议只锁语义
8. PR #7（W005）合并前，main 上少 18 项 W005 回归测试（techlab 侧
   不受影响；合并后总数自然归一）

## 7. 快速命令速查（Mac 侧）

```bash
git fetch origin
git checkout -b dev/mac-joint-debug origin/dev/windows-tech-lab-wave2
python3 -m pip install -r backend/requirements.txt   # 联调工具只需这组依赖
python3 tools/tech-lab/validation/gen_cross_vectors.py    # 重新生成向量（核对用）
# 对拍 T07:
#   读 test-vectors/tech-lab/t07-calibration-vectors.json，Swift 拟合 → diff
# 对拍 T08:
#   读 t08-projection-vectors.json 的 arkit_conversion + cases → diff
# T12 联调（后端先在 Windows 上跑，带 opt-in）:
python3 tools/tech-lab/validation/t12_upload_client.py <file> http://<win-ip>:8000
# 证据包验证:
python3 tools/tech-lab/validation/evidence_verifier.py <pkg> --json r.json
```
