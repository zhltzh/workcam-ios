# WorkCam W008 Mac 交接文档

基线：`dev/windows-mvp-w008`（PR #13 HEAD `c11c279` 之上）。全量 **492 passed / 0 failed**（470 基线 + 22 同步）；模拟客户端 8 步链对运行中后端实测通过。全程 **DESKTOP_SIMULATION**——真机结论由 Mac 产出。

## 0. 启动

```bash
cd backend
python3 -m pip install -r requirements.txt
# 需先注册设备拿到 token（同 W006/W007 流程，devices.jsonl 带外 enroll）
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 1. 模拟离线同步客户端

```bash
python3 tools/tech-lab/validation/ios_sync_sim.py \
    http://<win-ip>:8000 <device-token>
```

**Mac 真机替换点**：脚本内 `data = b"sim iPhone JPEG stand-in..."` 换成真实 iPhone JPEG 字节；同步操作的 `device_id` 换成真机标识；照片元数据的 `captured_at` 来自相机 EXIF（注意：这是**客户端声明**，服务端不信任）。

## 2. 同步 API 快览

```
POST /api/v1/sync/batch         提交离线队列（逐项回执 + id_map）
GET  /api/v1/sync/events         增量拉取（cursor + limit + entity_type）
```

认证：`X-Device-Token`（同 W006/W007）；跨主体 404 等价。

### 批量请求体

```json
{
  "operations": [
    {"op": "project_create",
     "operation_id": "客户端生成的唯一 ID",
     "payload": {"operation_id": "…", "local_id": "离线本地 ID",
                 "name": "Site A"}},
    {"op": "photo_register",
     "operation_id": "…",
     "payload": {"operation_id": "…", "local_id": "…",
                 "server_project_id": "第一批回执的 id_map 值",
                 "idempotency_key": "…", "original_sha256": "…",
                 "original_filename": "IMG.jpg", "mime_type": "image/jpeg",
                 "byte_size": 12345, "captured_at": "…",
                 "device_id": "…"}}
  ]
}
```

### 响应体

```json
{"batch_received": 2,
 "receipts": [{"operation_id": "…", "status": "applied",
               "result": {"server_project_id": "…", "revision": 1}},
              {"operation_id": "…", "status": "applied",
               "result": {"server_photo_id": "…"}}],
 "id_map": {"loc-proj": "uuid-…", "loc-photo": "uuid-…"}}
```

### 增量拉取

```bash
# 首次：cursor=0
curl -H "X-Device-Token: $TOKEN" \
  "http://…/api/v1/sync/events?cursor=0&limit=100"
# 后续：cursor=上次响应的 cursor 值
```

### 冲突重试（project_update）

```json
{"op": "project_update", "operation_id": "…",
 "payload": {"operation_id": "…", "server_project_id": "…",
              "expected_revision": 2, "name": "new name"}}
```

收到 `revision_conflict` → re-fetch → 合并 → 用新 revision 重试（见 w008-conflict-policy.md）。

## 3. 故障恢复场景（测试钉死）

| 场景 | 行为 |
|---|---|
| 客户端重复发送整批 | 全部 duplicate，零重执行 |
| 上传成功但回执丢失 | 幂等重放，字节不变 |
| 服务重启 | 台账/游标 DB 落库，不变 |
| 部分批次成功 | 逐项回执（applied/rejected 独立） |
| 游标非法/过期 | 422（负值）；游标永不过期（单调递增） |
| 两设备同改项目 | typed `revision_conflict`（绝不 LWW） |
| 跨主体重放 | 台账 owner 隔离——各自执行，互不干扰 |

## 4. 桌面 vs 真机

- 492 项测试 + 模拟客户端全链 = **桌面数据**
- 真机待验证：真实离线场景（飞行模式→恢复）、SE 密钥材料、HEIC 照片、真机签名走完 link→verify→回写、并发双设备实测

## 5. 同步顺序（Mac 实现参考）

```
1. (离线) 拍照 → 本地 SQLite 排队 {op, operation_id, payload}
2. (恢复) POST /api/v1/sync/batch（全部操作一次提交）
3. 解析 id_map → 本地 ID→服务端 ID 映射持久化
4. 对每张照片：PUT /api/v1/photos/{server_photo_id}/original（W007）
5. 对每条证据：POST .../evidence-links + verify（W006 冻结协议）
6. GET /api/v1/sync/events?cursor=<last> → 增量拉取服务端状态
7. 网络闪断 → 重发 batch → 全部 duplicate（安全）
```
