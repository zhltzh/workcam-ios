# TECH-MARATHON-01 · 进度记录（2026-11-25 深夜批次）

分支：`dev/windows-tm-security`（基于 RC2 `d543e9a`） · 全量回归 **1030 passed, 3 skipped / 0 failed**（5:32）

## 已完成任务（第一优先队列）

| 任务 | Commit | 内容 | 测试 |
|---|---|---|---|
| TM01 证据协议 Schema | `768d2b5` | 机器可读 `evidence-schema-v1.json` + 独立校验器；未知版本永不信任规则；canonical payload 重建对齐 W023 字节合同 | 10 |
| TM03 设备密钥生命周期 | `17cc5e3` | 旧钥持有证明的轮换（单次 nonce）、回滚防护（rotated-out 不得再注册）、可逆 suspended（v13 迁移重建表放宽 CHECK）、密钥历史审计、注册状态与签名数学分离；6 个新端点（rotate/suspend/resume/keys/keys/status）；契约资产再生成（71 paths/83 ops） | 7 |
| TM04 App Attest 服务端验证 | `d0822d4` | 零依赖 CBOR 解析器、证书链/nonce/app-id 绑定、assertion 签名 + counter 防重放、合成 CA 向量（明确非 Apple 材料，DEVICE_PENDING）、照片密钥绑定边界钉死 | 12 |
| TM05 证据审计链 | `9e4b5bc` | 分叉/删除/插入/替换检测（具名错误码）、Merkle 树+包含证明、签名外部检查点；**链尾截断本地不可检测**实验证明 + 检查点使其可检测 | 9 |
| TM07 十维可信引擎 | `483ab48` | 无总分设计；每维 reason/evidence/failure_condition 三件套；客户端声明永不提升；App Attest 无绑定=NOT_ATTESTED、外推时间=UNVERIFIED 等诚实规则 | 15 |

## 诚实边界（本批次钉死的）

1. App Attest 验证通过 ≠ 照片签名密钥来自 Secure Enclave（绑定需同钥挑战）
2. 合成 CA 向量不是 Apple 材料，真实 Apple 根交叉验证 DEVICE_PENDING
3. 本地哈希链不能证明链尾未被删除（TM05 实验 + 外部检查点方案）
4. 普遍 GNSS 声明最高只能到 UNVERIFIED（location_source 永不 VERIFIED）
5. 外推时间是估计区间，不是服务器认证的拍摄时间

## B02 收官（今晚早些时候）

- B02-W020 压测（`d543e9a`）：1k/5k/10k 三阶段真实百分位，0 并发错误
- B02-W028 商业化研究（`763befd`）：12 研究点 + 租户隔离 PoC 10 passed
- B02 全部 15 项任务 DESKTOP_VERIFIED

## 下一步（按第一优先队列剩余）

- TM02：OpenSSL 独立验证补充（继承 W023 已有 144 checks）
- TM06：验证 CLI 补 HTML 报告 + 单照片/文件夹模式（继承 W018）
- TM08：来源威胁模型文档
- TM09-TM12：时间锚点 V3、不确定性区间、GNSS 研究、定位质量检查
