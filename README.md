# Medical Report Processor

从 PDF 体检报告中提取量化指标和患者信息，生成标准化映射文件和数据文件。

## 项目结构

```
medical-report-processor/
├── SKILL.md                          # 技能说明文档
├── scripts/
│   └── process.py                    # 核心处理脚本
├── assets/
│   ├── mappings_template.csv         # 指标映射模板（核心配置）
│   └── data_template.csv             # 数据模板
└── references/
    └── medical_synonyms.md           # 医学同义词映射库
```

## 快速开始

```bash
# 安装依赖
pip install PyPDF2 pdfplumber pymupdf

# 处理PDF
python scripts/process.py report.pdf --both
python scripts/process.py report1.pdf report2.pdf --both
python scripts/process.py ./reports/ --mapping
```

---

## 增减指标操作指南

### 核心原则

本项目采用**模板驱动**设计，修改指标只需修改模板文件，无需修改代码。

---

### 添加新指标

**步骤1：修改映射模板** `assets/mappings_template.csv`

新增一行：
```csv
,血氧饱和度,oxygen_saturation,%,95--100,呼吸功能
```

**步骤2：同步修改数据模板** `assets/data_template.csv`

添加列名：
```csv
...,uric_acid,oxygen_saturation
```

**步骤3：（可选）添加同义词** `references/medical_synonyms.md`

```markdown
- 血氧饱和度, 血氧, SpO2 → oxygen_saturation
```

---

### 删除指标

**步骤1：** 在 `mappings_template.csv` 删除对应行

**步骤2：** 在 `data_template.csv` 删除列名

**步骤3：** 在 `medical_synonyms.md` 删除同义词

---

### 修改指标名称

**步骤1：** 修改 `mappings_template.csv` 中的 `canonical_name`

**步骤2：** 在 `medical_synonyms.md` 添加旧名称作为同义词

---

## 配置文件说明

### 1. `mappings_template.csv` - 核心配置

定义所有指标的标准名称、单位、参考范围。

| 列名 | 说明 | 是否必填 |
|------|------|----------|
| `hospital_name` | 医院实际名称 | 留空（自动填充） |
| `canonical_name` | 标准中文名称 | ✅ 必填 |
| `official_feature_name` | 标准英文名称 | ✅ 必填 |
| `official_unit` | 单位 | ✅ 必填 |
| `official_reference` | 参考范围 | ✅ 必填 |
| `official_group_zh` | 分类分组 | ✅ 必填 |

**⚠️ 重要：** CSV文件的行顺序决定了输出文件的列顺序，必须保持一致！

---

### 2. `data_template.csv` - 数据列顺序

仅包含列名（`official_feature_name`），必须与 `mappings_template.csv` 的顺序完全一致。

---

### 3. `medical_synonyms.md` - 同义词映射库

用于映射医院使用的非标准名称到标准名称。

格式：`医院名称_1, 医院名称_2 → canonical_name`

---

## 高级配置

如需修改提取逻辑，编辑 `scripts/process.py`：

- **新增单位**：修改第225-232行 `MEDICAL_UNITS` 列表
- **患者信息提取**：修改第148-217行 `extract_patient_info()` 函数
- **指标提取策略**：修改第379-511行 `extract_indicators_from_text()` 函数

---

## 验证配置

```bash
# 检查CSV格式
python -c "import csv; list(csv.DictReader(open('assets/mappings_template.csv', 'r', encoding='utf-8-sig')))"

# 测试处理
python scripts/process.py test_report.pdf --both
```

---

## 常见问题

**Q: 修改模板后，之前的映射文件还能用吗？**

A: 不能。需重新处理所有PDF，生成新的映射文件。

**Q: 程序如何处理不在模板中的指标？**

A: 静默丢弃，确保输出数据的一致性。

**Q: 如何支持新的PDF格式？**

A:
- 格式识别问题：修改 `process.py` 中的提取策略
- 指标名称变体：在 `medical_synonyms.md` 添加映射
- 单位变化：在 `MEDICAL_UNITS` 列表添加新单位

---

## 技术细节

- PDF解析库：PyPDF2 → pdfplumber → pymupdf（按优先级尝试）
- 输出编码：UTF-8 with BOM（确保Excel正确显示中文）
- 许可证：MIT License