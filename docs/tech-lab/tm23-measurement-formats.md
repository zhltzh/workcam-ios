# TM23 · 开放测量格式与 DXF 可行性研究

格式版本：`measurement-format/1.0`（代码 `backend/techlab/geometry/
measurement_format.py`）

## 交付的格式

| 格式 | 用途 | 诚实规则 |
|---|---|---|
| JSON | 结构化交换（含 JSON 往返解析与版本拒绝） | `format_version` 不符即拒；manual/device 的 `measurement_nature` 字段随值同行 |
| CSV | 表格导入 | 几何列引用 JSON 编码；空可选字段=空串 |
| GeoJSON | 地图叠加 | 只导有 `geodetic_anchor` 的记录，无锚点记录**计数跳过**（`skipped_no_anchor`），绝不编造坐标；RFC 7946 经度在前 |
| PLY | 点云 | 复用 TM21 导出器；记录归属写入 header comment（PLY 便携子集无逐点字符串属性——如实声明） |
| SVG | 工程示意图 | XY 投影草图；无已知参照长度时**不标比例**（文案写明） |
| DXF | CAD（实验性 PoC） | 最小 R12 ASCII 子集，见下 |

## 诚实规则（进数据模型本身，不靠文档自觉）

1. **manual/device 结构性分离**：`manual_input` 记录携带
   `measurement_mode` 直接被模型构造器拒绝——不是提示，是异常。
2. **未校准显式标记**：`calibration_version="uncalibrated"` 的设备
   估计在 JSON 的 `measurement_nature` 里写明 "device ESTIMATE … not
   a certified measurement"。
3. **与生产证据协议隔离**：本格式是 tech-lab 交换格式，证据链走
   签名照片管线（任务书规则：不得擅自纳入生产证据协议）。

## DXF 可行性结论（研究 + PoC）

- **可行性：确认可行（实验性）**。DXF R12（AC1009）是最后一个
  纯 ASCII、被所有主流 CAD（AutoCAD/LibreCAD/QCAD/DWG 看图器）
  普遍可读的方言；仅含 ENTITIES 节的扁平文件合法。
- 实现范围（`dxf_poc.py`）：LINE/POLYLINE/VERTEX/CIRCLE/TEXT 实体，
  图层名 `WCM-MEASURE-<measurement_id>` 保证导入可溯源。
- **未实现**（如实记录）：BLOCK、尺寸标注、剖面线、图纸空间、
  Unicode 转义细节、二进制 DXF。若产品确认需要完整 DXF，建议评估
  成熟库（ezdxf，MIT 许可）而非手写扩展——当前 PoC 刻意零依赖。
- 状态：POC_VERIFIED（序列化器有测试）；**实验性，不进生产**。

## Swift 侧（TM61 包）

`MeasurementRecord` 的 Codable 端口属于第三批扩展；JSON 结构即
Swift Codable 模型的字段合同（字段名一一对应，units 用原字符串）。
