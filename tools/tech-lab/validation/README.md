# Tech Lab Validation Tools

独立离线验证工具集。**不依赖 WorkCam App、不依赖任何在线服务器。**

## evidence_verifier.py — 证据包独立验签 CLI (T18/W-09)

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
# 可选锚点回执验证（需要可信服务器公钥 PEM + key_id）:
python tools\tech-lab\validation\evidence_verifier.py <package_dir> `
    --anchor-pubkey server_key.pem --anchor-key-id test-key-001
```

### macOS / Linux

```bash
python3 tools/tech-lab/validation/evidence_verifier.py <package_dir> --json report.json
python3 tools/tech-lab/validation/evidence_verifier.py <package_dir> \
    --anchor-pubkey server_key.pem --anchor-key-id test-key-001
```

### 状态与退出码

| 状态 | 含义 |
|---|---|
| `PASS` | 签名数学 + 哈希全部通过（且提供了注册表且公钥已注册） |
| `PASS (identity not checked: no trust registry provided)` | 数学通过；未提供注册表，**设备身份未核查**（如实说明） |
| `PASS_UNREGISTERED_KEY` | 数学通过；注册表明确不含该公钥 |
| `CANNOT_VERIFY` | 文件缺失等，**绝不显示为通过** |
| `FAIL` | 篡改/损坏，带具体记录与原因 |

退出码：`0` = 全部记录通过；`1` = 有失败或无法验证。

**边界声明**：本工具证明的是**字节完整性与签名数学**。签名有效
不等于拍摄现场真实；设备是否可信（App Attest）不在离线工具的
职责内——报告中逐条注明。

跨平台注意：CLI 内部以 UTF-8 读写输出（Windows 管道默认 GBK 会在
Python < 3.15 下崩溃读取线程；测试环境强制 `PYTHONIOENCODING=utf-8`
已固化在 `tests/test_techlab/test_evidence_verifier.py` 的 subprocess
用法里，供任何自动化调用参考）。

## t04_batch_review.py — Mac LiDAR CSV 自动验收 (W-03)

对 Mac LiDAR Lab（PR #9）导出的实验 CSV 做验收：格式、缺字段、
重复 test_id、未知模式、异常值、完成度（对照 132 条清单）、误差
统计、失败原因汇总。支持部分完成的数据。输出 JSON + Markdown。

### 用法

```powershell
# Windows
python tools\tech-lab\validation\t04_batch_review.py <experiment.csv> --json r.json --md r.md
```

```bash
# macOS / Linux
python3 tools/tech-lab/validation/t04_batch_review.py <experiment.csv> --json r.json --md r.md
```

默认对照 `docs/tech-lab/experiments/t04-checklist.csv`；可用
`--checklist <path>` 指定其他清单。

### 实验清单（唯一 test_id 的权威来源）

```bash
python3 tools/tech-lab/benchmarks/t04_checklist.py [out.csv]
# 生成 132 条：raycast 68 (primary) + scene_depth 32 + smoothed_depth 32 (secondary)
```

## 统计分析（W-02）

```bash
python3 tools/tech-lab/benchmarks/lidar_error_analysis.py <experiment.csv> --json stats.json
```

分组（模式/条件/距离/单元格）统计：成功率、失败原因分布、MAE、
RMSE、中位数、P95、有符号偏差、重复性离散度。样本 <3 → 全部
`null`（不输出 NaN/Infinity）。
