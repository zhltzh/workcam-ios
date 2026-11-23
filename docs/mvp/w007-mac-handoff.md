# WorkCam W007 Mac 交接文档

基线：`dev/windows-mvp-w007`（功能基线 PR #12 `0fef063`）。全部命令已在 Windows 实测：全量 **470 passed / 0 failed**；模拟客户端 8 步链对运行中的后端 **7/7 PASS**（桌面模拟数据）。

## 0. 启动

```bash
cd backend
python3 -m pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
# 业务库迁移自动执行（v3 幂等）；原图库 <db-dir>/originals/ 自动创建
# 认证：所有 /api/v1 需要 X-Device-Token（已注册设备令牌，同 W006）
```

## 1. 模拟 iOS 全链脚本（Mac 直接可跑）

```bash
# 1) 在后端注册一个设备（带外 enroll，写 devices.jsonl）拿到 token
# 2) 跑模拟客户端：
python3 tools/tech-lab/validation/ios_client_sim.py \
    http://<win-ip>:8000 <device-token>
```

脚本执行（实际输出，Windows 实测）：

```
1. create project          [PASS]
2. (本地照片字节)
3. register photo          [PASS] sha=90c4a574…
4. upload original         [PASS] stored=True
5. link evidence           [PASS] status=unverified
6. integrity view          [PASS] evidence=unverified
7. download + sha compare  [PASS] hdr=90c4a574… vs local 90c4a574…
8. tampered upload refused [PASS] code=400
RESULT: 7 passed, 0 failed
```

**Mac 真机替换点**：步骤 2 的 `data`（脚本内 `b"\x42"*2048`）换成真实 iPhone JPEG 的文件字节；密钥材料换成 SE 导出。步骤 5 的证据引用换成真机签名清单的 record_id，然后调 W006 的 verify 端点让服务端验证回写。

## 2. API 快览（详见 w007-original-storage.md 与 /openapi.json）

```
PUT  /api/v1/photos/{photo_id}/original           上传+绑定（原始字节 body）
GET  /api/v1/photos/{photo_id}/original           下载（X-Original-Sha256 头）
GET  /api/v1/photos/{photo_id}/original/integrity 完整性+证据链视图
```

错误码：`hash_mismatch`（400，零持久化）/ `binding_conflict`（409）/
`object_corrupted`（409，人工恢复）/ `original_not_uploaded`（404）。

## 3. 完整性 vs 真实性（每次响应都带）

```json
"claims": {
  "file_integrity_verified": true,
  "photo_authenticity_proven": false,
  "authenticity_note": "storage verifies bytes only; ..."
}
```

存储验证字节；真实性要靠证据链接的服务端验证（W006 verify 端点）——且即便验证通过也只是签名数学/锚点验证，不证明现场真实。

## 4. 备份/恢复/fsck（运维命令）

```python
from app.services.original_store import fsck, backup, restore
fsck(fix_temps=False)   # 审计报告（损坏/缺失/孤儿/残渣；绝不自动删）
fsck(fix_temps=True)    # 只清崩溃 .part 残渣
backup(Path("backup-originals"))          # DB 用 VACUUM INTO（W006）+ 对象逐个复验
restore(Path("backup-originals"), manifest)  # 幂等；损坏现存对象拒绝覆盖
```

## 5. 桌面 vs 真机边界

- 470 项测试与 7 步链全部**桌面数据**
- 真机待验证：真实 JPEG（HEIC 转换路径、大尺寸）、SE 密钥材料、真机签名清单走完 link→verify→回写全链
- `captured_at`/`device_id` 仍是客户端声明（W006 trust_notes 语义不变）
