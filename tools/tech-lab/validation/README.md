# Tech Lab Validation Tools

独立离线验证工具集。**不依赖 WorkCam App、不依赖任何在线服务器。**

## evidence_verifier.py — 证据包独立验签 CLI (T13/T18)

验证一个 T13 证据包：文件完整性、P-256 签名、跨记录绑定、衍生图
回链、锚点回执（可选）。输出人类可读报告 + 机器 JSON。

### 依赖（Windows 与 macOS 相同）

```bash
# 需要 Python 3.12+；依赖只来自 backend/requirements.txt
# (pydantic, cryptography —— 纯本地，无网络调用)
cd <repo>
python -m pip install -r backend/requirements.txt
```

### Windows

```powershell
python tools\tech-lab\validation\evidence_verifier.py <package_dir> --json report.json
```

### macOS / Linux

```bash
python3 tools/tech-lab/validation/evidence_verifier.py <package_dir> --json report.json
```

状态与退出码：`0` = 全部记录通过；`1` = 有失败/无法验证。
`PASS / PASS_UNREGISTERED_KEY / CANNOT_VERIFY / FAIL` 四态语义见
`--help` 与代码 docstring；**签名有效 ≠ 拍摄现场真实**（报告逐条注明）。

## t12_upload_client.py — 上传协议联调客户端 (W003)

对**运行中的 FastAPI 后端**执行完整协议流程（乱序上传→故意丢块→
status 续传→重复块幂等→complete 哈希核对），逐步打印日志：

```powershell
# Windows (后端先启动: tools\start_backend.ps1)
python tools\tech-lab\validation\t12_upload_client.py <file> http://127.0.0.1:8000
```

```bash
# macOS / Linux
python3 tools/tech-lab/validation/t12_upload_client.py <file> http://127.0.0.1:8000
```

退出码 `0` = 服务器回执哈希与本地文件哈希一致。纯标准库，双平台。

端点前缀 `/upload/experimental`（生产环境 `WORKCAM_ENV=production` 禁用）。

## 跨平台测试向量与 JSON Schema (T07/T08)

`test-vectors/tech-lab/` 下的三份文件是 Mac 对拍的唯一依据：

| 文件 | 内容 | Mac 用法 |
|---|---|---|
| `t07-calibration-vectors.json` | 仿射拟合 4 例（含共线/残差拒绝） | 用 SIMD/自写解出 M/t，与 `expected_M/expected_t` 1e-9 对拍 |
| `t08-projection-vectors.json` | 投影 6 例（含后方点拒绝） | `p_cam = R(q)@p_world+t; u=fx*xc/zc+cx`，与 `expected_uv` 1e-9 对拍 |
| `record-schemas.json` | ARFrameRecord / DrawingSheet 的 JSON Schema | 生成 Codable 结构体；**units 字段必填**（pixels/mm_35mm_eq） |

注意：T08 协议约定 **+z 为相机前方**；ARKit 原生相机看向 -z，Mac
序列化时必须一致转换（向量文件的 `_convention` 字段已声明）。

重新生成：`python tools/tech-lab/validation/gen_cross_vectors.py`

## t04_batch_review.py — Mac LiDAR CSV 自动验收 (W-03)

```bash
python3 tools/tech-lab/validation/t04_batch_review.py <experiment.csv> --json r.json --md r.md
```

支持部分完成数据；默认对照 `docs/tech-lab/experiments/t04-checklist.csv`。

