# WorkCam W006 Mac 交接文档

基线：`dev/windows-mvp-w006`（功能基线 `8a05391`）。全部命令已在 Windows
实测通过（440 项测试全绿）。以下均为**桌面验证**——真实 iPhone 数据的
结论必须由 Mac 真机产生。

## 0. 启动与初始化

```bash
# 1. 依赖（无新增——现有 requirements.txt 即可）
cd backend
python3 -m pip install -r requirements.txt

# 2. 启动（业务库迁移在启动时自动执行，幂等；默认 data/business/workcam.db）
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# 3. （可选）自定义业务库路径
WORKCAM_BUSINESS_DB=/path/to/workcam.db python3 -m uvicorn app.main:app
```

## 1. 设备注册（一次性）

`/api/v1` 全部端点要求 `X-Device-Token`——即 upload_auth 的设备令牌。
注册方式与上传鉴权相同（复用同一个 `devices.jsonl`，`WORKCAM_DEVICE_REGISTRY`
路径可配）：

```python
# Mac 侧：用证据签名的同一把 P-256 公钥注册（一个身份，不新增凭据）
# token = sha256(public_key_b64)——由 enrollment 工具/脚本写入注册表文件
```

> 联调期最简做法：让 Windows 侧在 `devices.jsonl` 里为 Mac 的公钥
> enroll 一行（带外操作，与上传端点鉴权同一流程）。

## 2. 全链路 Mac 调用示例（创建项目 → 登记照片 → 关联证据 → 查询）

```bash
TOKEN="<your-device-token>"
H="X-Device-Token: $TOKEN"
BASE="http://<windows-ip>:8000/api/v1"

# 2.1 创建项目
curl -s -X POST "$BASE/projects" -H "$H" -H "Content-Type: application/json" \
  -d '{"name":"Site A","description":"desktop demo"}'
# → {"project_id":"<pid>", "status":"active", ...}

# 2.2 登记照片（元数据；sha256 为 Mac 本地计算）
PHOTO_SHA=$(shasum -a 256 IMG_0001.jpg | cut -d' ' -f1)
curl -s -X POST "$BASE/projects/<pid>/photos" -H "$H" \
  -H "Content-Type: application/json" \
  -d "{\"idempotency_key\":\"mac-key-001\",\"original_sha256\":\"$PHOTO_SHA\",
       \"original_filename\":\"IMG_0001.jpg\",\"mime_type\":\"image/jpeg\",
       \"byte_size\":$(stat -f%z IMG_0001.jpg),
       \"captured_at\":\"2026-11-23T08:00:00Z\",\"device_id\":\"iPhone15,3\"}"
# → {"photo_id":"<photoid>", ..., "trust_notes":{...client-claimed, untrusted}}

# 2.3 关联证据（引用 = 既有 /verify 系统里的 record_id）
curl -s -X POST "$BASE/photos/<photoid>/evidence-links" -H "$H" \
  -H "Content-Type: application/json" \
  -d '{"evidence_reference":"<record_id>","evidence_type":"signature_manifest"}'
# → verification_status:"unverified"（可信状态只能由服务端验证写入）

# 2.4 服务端验证回写（提交材料；状态由服务端计算）
curl -s -X POST "$BASE/photos/<photoid>/evidence-links/<linkid>/verify" -H "$H" \
  -H "Content-Type: application/json" \
  -d '{"manifest": {…完整签名清单…}, "photo_base64": "…可选，字节级复核…"}'
# → 签名有效+公钥已注册: cryptographically_valid
# → 签名有效+公钥未注册: signature_valid（reason 注明设备身份未确立）
# → 任一失败: verification_failed + failure_reason（可重跑恢复）

# 2.5 查询项目照片
curl -s "$BASE/projects/<pid>/photos?limit=20" -H "$H"

# 2.6 查看证据状态
curl -s "$BASE/photos/<photoid>/evidence-links" -H "$H"
```

## 3. OpenAPI 文档

服务运行时：`http://<windows-ip>:8000/openapi.json`（Swagger UI：
`/docs`）。`/api/v1` 全部 11 个操作已在 schema 中。

## 4. 回归命令

```bash
cd backend
# 全量（440 = 409 基线 + 31 W006）
python3 -m pytest tests -q
# 仅 W006 业务测试
python3 -m pytest tests/test_mvp_api.py -q
```

## 5. 桌面 vs 真机边界（如实）

- 本轮全部验证为**桌面测试**（软件测试密钥、模拟设备令牌）
- `device_id` / `captured_at` 是客户端声明数据，服务端永不标记可信
- 证据状态闭环已实现：verify 端点由服务端运行既有验证路径并回写
  `signature_valid` / `cryptographically_valid` / `server_anchored` /
  `verification_failed`——但全部验证样本仍为桌面合成数据
- 真机需要验证：SE 公钥注册流程、真实照片登记、真机签名清单走完
  关联→验证→回写全链（大文件传输走既有实验上传通道）

## 6. 已知限制

1. 照片登记只存元数据——原图二进制的业务化存储（非实验上传）在后续轮
2. 设备注册表仍为文件型（`devices.jsonl`）——与上传鉴权共用的既有边界
3. `signature_valid` 状态的语义与既有 /verify 的同名状态一致（数学
   有效、身份未确立）——永远不夸大为 cryptographically_valid
