# 验收矩阵 (Acceptance Matrix)

每项技术对应的交付物与**可执行证据**。更新状态时必须附测试命令与结果。

图例：✅ 本轮已交付并测试；◐ 部分交付（真机在 Mac）；— 未开始；⏳ Wave 2+。

| # | 状态 | 研究+资料 | PoC/不可行证明 | 自动化测试 | 性能/精度指标 | 真机验收 |
|---|---|---|---|---|---|---|
| T01 | RESEARCHED→POC_READY | ✅ ADR-0001 + W005-2 对照 | ✅ 密钥生命周期协议模型 | ✅ `test_techlab/test_key_lifecycle.py` | 协议行为级（真机指标待 Mac） | ⏳ 脚本待真机 |
| T02 | POC_READY | ✅ risk-register T02 | ✅ `techlab/location_meta.py` | ✅ `test_techlab/test_location_meta.py` | ✅ 质量分级阈值表 | ⏳ Core Location 实测 |
| T03 | POC_READY | ✅ W003 边界 + 本轮异常分类 | ✅ `techlab/time_integrity.py` | ✅ `test_techlab/test_time_integrity.py` | ✅ 8 类异常检出率表 | —（纯算法） |
| T04 | RESEARCHED | ✅ ARKit 能力研究 | ◐ 实验方案（`experiments/t04-*.md`） | ◐ CSV 分析工具 `benchmarks/lidar_error_analysis.py`（带自测数据） | ⏳ 待真机原始数据 | ⏳ 全部 |
| T05 | POC_READY | ✅ ADR-0002 | ✅ `techlab/derivative_model.py` | ✅ `test_techlab/test_derivative_model.py` | 防覆盖 100% 用例 | ⏳ 渲染待 Mac |
| T06 | LIMITED | ✅ 现有原子写盘点 | — | ⏳ Wave 2 故障注入 | — | ⏳ |
| T07–T12, T14 | NOT_STARTED | — | — | — | — | — |
| T13 | RESEARCHED | ✅ 绑定方式盘点（W002-R3/W005） | ⏳ Wave 2 证据包格式 | — | — | — |
| T15–T17 | NOT_STARTED | — | — | — | — | — |
| T18 | LIMITED | ✅ 现有工具差距分析 | ⏳ Wave 2 离线 CLI | — | — | — |

## 本轮新增测试的证据索引

全部命令在仓库根执行（依赖 `backend/.venv`）：

```powershell
Push-Location backend; .venv\Scripts\python.exe -m pytest tests\test_techlab -v; Pop-Location
# 预期：test_techlab 全部通过（数量见当轮 Wave 报告）

Push-Location backend; .venv\Scripts\python.exe -m pytest tests -q; Pop-Location
# 预期：157（既有）+ techlab 新增全部通过，无回归
```

## 真机验收模板（iPhone 专属，随实验方案交付）

每个真机实验必须回填 `experiments/<id>-results.md`：

```markdown
## T0X 真机结果
- 设备型号 / iOS 版本 / 日期：
- 执行步骤与实际输出：（原始数据文件路径 datasets/）
- 成功/失败/失败率：
- 与 Windows 侧预期的一致性：
- 结论：状态从 DEVICE_PENDING 改为 DEVICE_VERIFIED / LIMITED / REJECTED
```

没有此报告，状态永远停留在 DEVICE_PENDING。
