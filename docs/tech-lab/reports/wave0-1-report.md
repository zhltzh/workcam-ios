# Wave 0 + Wave 1（Windows 侧）阶段报告

日期：2026-11-19 · 分支：`dev/windows-tech-lab`（基于 main `17d9699`）

## 完成技术编号

| Wave | 技术 | 状态变化 | 交付 |
|---|---|---|---|
| 0 | 审计/目录/矩阵 | — | 5 份文档 + ADR×2 + 审计报告 |
| 1 | T01 | RESEARCHED → **POC_READY**（协议层） | G1–G4 生命周期建模 + 4 项测试 + 真机验收脚本 |
| 1 | T02 | NOT_STARTED → **POC_READY** | 元数据 v2（source/quality/防伪造语义）+ 8 项测试 |
| 1 | T03 | POC_READY(锚点) → **POC_READY**(完整) | 6 类时钟异常检测 + 9 项测试 |
| 1 | T04 | NOT_STARTED → **RESEARCHED** | 真机实验方案（96 次测试矩阵）+ CSV 分析工具（3 项自测） |
| 1 | T05 | NOT_STARTED → **POC_READY** | 不可变原图 + 衍生图回链模型 + 7 项测试 |

## 测试通过情况

```text
pytest tests/test_techlab  →  31 passed
pytest tests（全量）      →  170 passed（139 main 基线 + 31 techlab，0 失败 0 回归）
```

注：main 基线为 139（W005 的 18 项新增在 PR #7 分支，尚未合并）。

## 关键技术决策

1. **ADR-0001**：SE 密钥失效按"安全边界"设计——历史证据可信性不依赖设备持有私钥（公钥存档 + 锚点即可离线复验），换机/重装是显式新设备流程。
2. **ADR-0002**：原图 write-once；衍生图必须携带 `original_photo_hash` 回链且哈希不得等于原图（防冒充原件）。
3. **T03 诚实性上限**：无锚点会话 confidence 最高 `low`；有锚点且无异常最高 `medium`——离线设备时钟本质不可信，检测器只报"内部一致"，永不报"时间真实"。
4. **T02 语义强制**：manual 来源永不出现在"verified"标签中；network 离线定位强制封顶 C 级。

## 未解决 / 依赖真机

- T01 G1–G8 真机判定：`experiments/t01-se-acceptance.md`（Mac）
- T04 全部实测数据：`experiments/t04-lidar-experiment.md`（Mac），当前零真机数据，状态按规则停在 DEVICE_PENDING
- T05 衍生图实际渲染与 sidecar 在 iOS 端的落地（Wave 2 前置）

## 下一 Wave 计划（Wave 2）

T06 故障注入框架 → T11（1 万条基准）→ T09（100/500/1000 张流式导出基准）→ T10（声明式水印模板引擎，消费 ADR-0002 模型）→ T13（证据包 v1 格式 = 原图+签名+衍生图+回链校验）→ T18（离线独立验签 CLI，消费 T13）。
