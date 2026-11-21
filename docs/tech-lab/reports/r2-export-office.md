# R2 交付报告：T09 导出内存基准 + DOCX/XLSX 实开验证

日期：2026-11-20 · 分支：`dev/windows-tech-lab-wave2`（PR #10）
任务来源：WC-W-TECH-002-R2 第二、三阶段

## 一、T09 导出内存基准（R2 修正版）

### R2 发现的旧基准缺陷

旧基准（W-06）的 `ExportItem.data` 是**必填 bytes**——1000 张基准的输入
列表本身驻留约 200 MB 照片字节；而 tracemalloc 只统计**引擎自身**的
Python 分配，把输入占着内存这一事实完全隐藏了。"峰值内存 2.31 MB"
只对"引擎开销"成立，**对整个导出过程不成立**。这是 R2 审核指出的
实质问题，已修复。

### 修复内容

| 修复 | 说明 |
|---|---|
| 文件路径输入源 | `ExportItem.file_path`（优先）与 `data`（小 fixture 用）双源；`iter_chunks()` 统一 256 KiB 流式读取，文件源的照片字节**全程不进进程内存** |
| 进程级 RSS 测量 | `RssMeter` 读取 Windows `GetProcessMemoryInfo` 的 **PeakWorkingSetSize**（真实进程高水位，含非 Python 缓冲），与 tracemalloc 引擎分配**分列报告** |
| 真实磁盘基准 | `benchmark_real_files()`：真实磁盘文件 100/500/1000 张，分四阶段（写盘基线 / 构建条目 / STORED 导出 / DEFLATED 导出）分别记录 |
| 边界测试 | 低磁盘（budget 注入）、重复文件名（导出前拒绝）、**路径前缀冲突**（`report` vs `report/main.jpg`——zipfile 本身不拦截，引擎预检必须拦）、导出中断（.part 不冒充成品） |

### 实测基准（DESKTOP：Win / Python 3.12 / 256 KiB 块；合成数据）

| 张数 | payload | 导出（STORED） | 进程 RSS 峰值 | RSS 增量 | DEFLATED | 引擎分配 |
|---|---|---|---|---|---|---|
| 100 | 19.5 MB | 0.08 s | 21.6 MB | +1.1 MB | 0.18 s / 43 KB | 0.54 MB |
| 500 | 97.7 MB | 0.35 s | 24.2 MB | +2.3 MB | 0.84 s / 212 KB | 1.37 MB |
| 1000 | 195.3 MB | 0.79 s | 27.3 MB | +3.0 MB | 1.83 s / 424 KB | 2.39 MB |

**关键结论**：1000 张 195 MB 流式导出，进程 RSS 峰值 27.3 MB——
其中约 24 MB 是 Python 解释器基线，**导出增量仅 3 MB**，照片字节
零驻留（对比：旧内联方式同规模进程 RSS >400 MB，实测对照见
`test_file_source_vs_inline_rss_gap`）。

### 自动化验证（全部真实运行）

- `test_export_rss.py`（8 项）：子进程 RSS 断言（500×400KB 流式 <25%
  payload）、文件源 vs 内联源 RSS 对照、重复名/前缀冲突拒绝、低磁盘
  中断（.part 留存、无成品）、中断恢复、字节完整性、RSS 字段存在
- `test_export_engine.py`（9 项回归全绿）：旧断言已按新语义修正

## 二、DOCX/XLSX 图片嵌入与真实 Office 验证

### 嵌入实现（标准 OOXML，非私有扩展）

- **DOCX**：inline DrawingML picture → `word/media/imageN.jpg` +
  `word/_rels/document.xml.rels`；显示尺寸固定 320×240（本 PoC 不解析
  JPEG 尺寸——iOS ImageIO 职责，如实记录）；`max_images=50` 防膨胀
- **XLSX**：完整 SpreadsheetML drawing 链：`sheet1.xml <drawing r:id>`
  → `sheet1.xml.rels` → `drawings/drawing1.xml`（oneCellAnchor）→
  `drawing1.xml.rels` → `xl/media/imageN.jpg`，锚定与元数据同列

### 结构验证（`test_office_embed.py`，14 项）

嵌入媒体字节与源文件逐字节相等；图片↔元数据对应（drawing 顺序=
条目顺序、rId 解析目标、锚定行=数据行）；中英文/emoji/特殊 XML
字符/600 字长文本在两格式 XML 转义与解析树双重验证。

### 真实 Office 实开验证（`test_office_real_app.py`，3 项）✅

本机装有 **Microsoft Office 2007**（.docx/.xlsx 的原生版本），通过
COM 自动化驱动**真实 Word / Excel**：

| 验证 | Word 2007 | Excel 2007 |
|---|---|---|
| 打开生成文件 | ✅ Documents.Open 成功 | ✅ Workbooks.Open 成功 |
| 嵌入图片 | ✅ InlineShapes.Count == 3 | ✅ Sheet.Shapes.Count == 3 |
| 中文内容 | ✅ 文件名+中文注释在正文 | ✅ 单元格读回 CJK/emoji/长文本 |
| 元数据行 | ✅ | ✅ 行 2-4 与 photo_id/note 一致 |

如实记录的两个**已验证的兼容性边界**（非缺陷）：

1. Excel 单元格语义会修剪尾部空格（正常行为，测试已按 `rstrip` 断言）
2. 显示尺寸固定（无 JPEG 尺寸解析）——嵌入 PoC 的既定边界

**Office 环境不可用时**该套件显式 SKIP 并声明 UNVERIFIED（诚实规则
写入代码），绝不出现"跳过=通过"。

## 三、已知限制与剩余风险

1. **iPhone**：以上全部为桌面数字；iOS 端流式导出、HEIC 编码、
   Core Text PDF 仍待 Mac（验收矩阵已标 DEVICE_PENDING）
2. Office 2007 为验证版本；Office 365/WPS 未装于本机，未验证（不外推）
3. DOCX/XLSX 嵌入的显示尺寸固定，产品化需 iOS 侧按实际像素适配
4. DEFLATED 对合成数据压缩比异常高（43 KB/195 MB）——真实 JPEG
   压缩收益会小得多，部署决策需用真实照片数据重测
5. `pywin32` 已加入测试环境（COM 验证用）；未进 requirements.txt
   （非引擎依赖，仅验证机需要）
