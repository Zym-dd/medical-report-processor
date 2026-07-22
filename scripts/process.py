"""
Medical Report Processor - Core Processing Script
===================================================
Processes PDF medical examination reports, extracts indicators,
generates mapping files and data files.

Usage:
    python scripts/process.py <pdf_paths...> [--mapping] [--data] [--both]

Examples:
    python scripts/process.py report.pdf --both
    python scripts/process.py report1.pdf report2.pdf --both
    python scripts/process.py ./reports/ --mapping
"""

import argparse
import csv
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ============================================================
# Configuration — Load template
# ============================================================

def load_mappings_template() -> List[Dict[str, str]]:
    """Load the fixed mappings template with 52 standard items."""
    skill_dir = Path(__file__).parent.parent
    template_path = skill_dir / "assets" / "mappings_template.csv"

    with open(template_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        return list(reader)


# ============================================================
# PDF Reading
# ============================================================

def collect_pdf_files(paths: List[str]) -> List[Path]:
    """Collect PDF files from paths (files or directories)."""
    pdf_files = []
    for p in paths:
        path = Path(p)
        if path.is_dir():
            pdf_files.extend(sorted(path.rglob("*.pdf")))
        elif path.is_file() and path.suffix.lower() == ".pdf":
            pdf_files.append(path)
        else:
            print(f"  [SKIP] Not a PDF: {p}")
    return pdf_files


def read_pdf_text(pdf_path: Path) -> str:
    """Extract text from a PDF file. Tries multiple backends."""
    # Try PyPDF2 first
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(str(pdf_path))
        text = "\n".join(
            page.extract_text() or "" for page in reader.pages
        )
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # Try pdfplumber
    try:
        import pdfplumber
        with pdfplumber.open(str(pdf_path)) as pdf:
            text = "\n".join(
                page.extract_text() or "" for page in pdf.pages
            )
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception:
        pass

    # Try pymupdf (fitz)
    try:
        import fitz
        doc = fitz.open(str(pdf_path))
        text = "\n".join(
            page.get_text() for page in doc
        )
        doc.close()
        if text.strip():
            return text
    except ImportError:
        pass
    except Exception:
        pass

    print(f"  [WARN] Cannot read PDF text (no PDF library available): {pdf_path.name}")
    print(f"         Install one of: PyPDF2, pdfplumber, or pymupdf")
    return ""


# ============================================================
# Hospital Detection
# ============================================================

def detect_hospital_name(text: str) -> str:
    """Detect hospital name from PDF text."""
    patterns = [
        # Pattern 1: XX医院体检报告 / XX医院健康体检报告
        r'([\u4e00-\u9fa5]{2,20}(?:医院|体检中心|健康管理中心|卫生服务中心|医疗中心|卫生院))\s*(?:体检|健康体检|健康检查)',
        # Pattern 2: 体检机构/医院名称 header
        r'(?:体检机构|医院名称|体检单位|医疗机构)[：:\s]*([\u4e00-\u9fa5]{2,20}(?:医院|体检中心|健康管理中心|卫生服务中心|医疗中心|卫生院))',
        # Pattern 3: Standalone hospital name near the top of the report
        r'^([\u4e00-\u9fa5]{2,20}(?:医院|体检中心|健康管理中心|医疗中心))\s*$',
    ]

    lines = text.split("\n")
    first_30_lines = "\n".join(lines[:30])

    for pattern in patterns:
        match = re.search(pattern, first_30_lines, re.MULTILINE)
        if match:
            name = match.group(1).strip()
            if len(name) >= 4:
                return name

    # Fallback: find any institution-like name
    fallback = re.search(
        r'([\u4e00-\u9fa5]{2,20}(?:医院|体检中心|健康管理中心|医疗中心))',
        first_30_lines
    )
    if fallback:
        return fallback.group(1).strip()

    return "Unknown_Hospital"


# ============================================================
# Patient Info Extraction
# ============================================================

def extract_patient_info(text: str) -> Dict[str, str]:
    """Extract patient basic info from PDF text."""
    info = {}

    # Name (姓名)
    name_patterns = [
        r'姓\s*名[：:]\s*([^\s\n\d]{2,4})',
        r'受检者[：:]\s*([^\s\n\d]{2,4})',
        r'被检人[：:]\s*([^\s\n\d]{2,4})',
        r'客户姓名[：:]\s*([^\s\n\d]{2,4})',
    ]
    for pat in name_patterns:
        m = re.search(pat, text)
        if m:
            name = m.group(1).strip()
            # Filter out non-name text
            name = re.sub(r'[^\u4e00-\u9fa5]', '', name)
            if 2 <= len(name) <= 4:
                info['sample_name'] = name
                break
    if 'sample_name' not in info:
        info['sample_name'] = 'Unknown'

    # Gender (性别)
    gender_pat = re.search(r'性\s*别[：:]\s*(男|女)', text)
    if gender_pat:
        info['sex'] = gender_pat.group(1)
    else:
        # Fallback: look for standalone 男/女 near age
        gender_fallback = re.search(r'[男女]\s*[·•]?\s*\d{1,3}', text)
        if gender_fallback:
            info['sex'] = gender_fallback.group(0)[0]

    # Age (年龄)
    age_pat = re.search(r'年\s*龄[：:]\s*(\d{1,3})', text)
    if age_pat:
        info['age'] = age_pat.group(1)
    else:
        age_fallback = re.search(r'[男女]\s*[·•]?\s*(\d{1,3})\s*岁', text)
        if age_fallback:
            info['age'] = age_fallback.group(1)

    # Exam year (体检日期 → extract year)
    year_patterns = [
        r'体检日期[：:]\s*(\d{4})',
        r'检查日期[：:]\s*(\d{4})',
        r'报告日期[：:]\s*(\d{4})',
        r'(\d{4})[-/年]\d{1,2}[-/月]\d{1,2}',  # Date format
    ]
    for pat in year_patterns:
        m = re.search(pat, text)
        if m:
            info['exam_year'] = m.group(1)
            break

    # ID (档案号/登记号/身份证号)
    id_patterns = [
        r'档案号[：:]\s*(\d{10,20})',
        r'登记号[：:]\s*(\d{10,20})',
        r'编号[：:]\s*(\d{10,20})',
        r'身份证号[：:]\s*(\d{15,18})',
        r'(\d{15,18})',  # Fallback
    ]
    for pat in id_patterns:
        m = re.search(pat, text)
        if m:
            info['ID'] = m.group(1)
            break

    return info


# ============================================================
# Indicator Extraction
# ============================================================

# Common medical units (sorted by length descending for greedy matching)
MEDICAL_UNITS = sorted([
    '10^9/L', '10^12/L', 'mmol/L', 'μmol/L', 'umol/L',
    'ml/min/1.73m^2', 'ml/min', 'mIU/L', 'ng/ml',
    'KU/L', 'U/L', 'g/L', 'mg/L', '次/分钟', '次/分',
    '/μL', 'mmHg', 'kg/m^2',
    'Kg', 'kg', 'Cm', 'cm', 'fL', 'fl', 'pg', '%',
    'mmol', 'BPM',
], key=len, reverse=True)


def _join_split_names(text: str) -> str:
    """Pre-process: join indicator names split across PDF lines.

    Pattern: name_line_ending_in_chinese + number_line + trailing_chinese
    Example:
      丙氨酸氨基转移
      12.80 7-40 U/L
      酶
    Becomes:
      丙氨酸氨基转移酶 12.80 7-40 U/L

    Also handles: name + number_line where trailing chars are on same line:
      平均红细胞血红
      333.00 316-354 g/L
      蛋白浓度
    Becomes:
      平均红细胞血红蛋白浓度 333.00 316-354 g/L
    """
    lines = text.split('\n')
    result = []
    i = 0
    while i < len(lines):
        line = lines[i]
        # Only join if: line ends with Chinese AND does NOT already contain
        # a complete value+unit or value+ref pattern
        has_value = bool(re.search(r'\d+\.?\d*\s+(?:[a-zA-Z/%μ^²³]+|次/|[Uu]/[Ll]|mm[oO]l|kg|cm|mmHg)', line))
        has_ref_range = bool(re.search(r'(?<!\d{4})\d{1,3}\.?\d*\s*[－—\-~至到]+\s*\d+', line))

        if (re.search(r'[\u4e00-\u9fa5]$', line) and
            not re.search(r'\d+\s*$', line) and
            not has_value and
            not has_ref_range and
            i + 1 < len(lines)):

            next_line = lines[i + 1]
            # Next line starts with a number (likely the value)
            if re.match(r'^\s*\d+\.?\d*', next_line):
                # Check if line+2 exists and is short Chinese (the name tail)
                name_tail = ''
                if i + 2 < len(lines):
                    third = lines[i + 2].strip()
                    if (re.match(r'^[\u4e00-\u9fa5()（）\w]{1,10}$', third) and
                        not re.match(r'^\d', third)):
                        name_tail = third
                        i += 1  # consume the tail line

                # Join: name_line + name_tail + space + value line
                joined = line.rstrip() + name_tail + ' ' + next_line.strip()
                result.append(joined)
                i += 2  # consumed 3 lines (name, value, tail) or 2 (name, value)
                continue

        result.append(line)
        i += 1

    return '\n'.join(result)


def _second_pass_scan(text: str, template_canonical: set,
                      existing: Dict[str, Dict]) -> Dict[str, Dict]:
    """Second-pass: relaxed scanning for indicators missed in first pass.

    Used when first-pass extraction misses items. Uses broader patterns:
    - Colon format in paragraphs: name：valueU/L
    - Summary format: 小结：.*name: value
    - Inline name-value: name value units in any context
    """
    new_results = {}

    # --- Pattern A: Colon in paragraphs with possible unit attached ---
    # Matches: 丙氨酸氨基转移酶：76.10U/L, 低密度脂蛋白胆固醇：4.11mmol/L
    colon_with_unit = re.compile(
        r'([\u4e00-\u9fa5a-zA-Z()（）]{2,30})[：:]\s*(\d+\.?\d*)\s*([a-zA-Z/%μ^²³]+(?:/[a-zA-Z]+)?)'
    )
    for m in colon_with_unit.finditer(text):
        name = m.group(1).strip()
        value = m.group(2)
        unit = m.group(3)
        if name not in existing and name not in new_results:
            new_results[name] = {
                'value': value, 'unit': unit, 'original_name': name
            }

    # --- Pattern B: Summary/bullet format ---
    # Matches: 小结：红细胞分布宽度(SD): 48.40 fL
    #         1、锌: 7.80 μmol/L
    #         2、高密度脂蛋白胆固醇: 2.38 mmol/L
    summary_pat = re.compile(
        r'(?:\d+[、.]?\s*|、\s*|小结[：:]?\s*)'
        r'([\u4e00-\u9fa5a-zA-Z()（）（）]{2,30})[：:]\s*'
        r'(\d+\.?\d*)\s*([a-zA-Z/%μ^²³]+(?:/[a-zA-Z]+)?)?'
    )
    for m in summary_pat.finditer(text):
        name = m.group(1).strip()
        value = m.group(2)
        unit = m.group(3) or ''
        # Filter out non-indicator text
        skip = ['过氧化氢', 'PH值', '白细胞酯酶', '阴道', '白带', '清洁度']
        if any(s in name for s in skip):
            continue
        if name not in existing and name not in new_results:
            new_results[name] = {
                'value': value, 'unit': unit, 'original_name': name
            }

    # --- Pattern C: Name-value-unit in same line (value AFTER ref_range) ---
    # Matches: 丙氨酸氨基转移酶 7-40 U/L 12.80
    broad_table = re.compile(
        r'([\u4e00-\u9fa5a-zA-Z()（）]{2,30})\s+'
        r'(?:[≤≥<>\d][\d.\-—~至到\s]*?)\s+'
        r'(' + '|'.join(re.escape(u) for u in MEDICAL_UNITS if len(u) >= 2) + r')\s+'
        r'(\d+\.?\d*)'
    )
    for m in broad_table.finditer(text):
        name = m.group(1).strip()
        unit = m.group(2)
        value = m.group(3)
        if name not in existing and name not in new_results:
            new_results[name] = {
                'value': value, 'unit': unit, 'original_name': name
            }

    # --- Pattern C2: name value ref_range unit (value BEFORE ref_range) ---
    # Matches: 丙氨酸氨基转移酶 12.80 7-40 U/L
    #         天门冬氨酸氨基转移酶 20.80 15-35 U/L
    #         低密度脂蛋白胆固醇 2.35 <3.37 mmol/L
    name_val_ref_unit = re.compile(
        r'([\u4e00-\u9fa5a-zA-Z()（）]{2,30})\s+'
        r'(\d+\.?\d*)\s+'
        r'(?:[≤≥<>\d][\d.\-—~至到\s]*?)\s+'
        r'(' + '|'.join(re.escape(u) for u in MEDICAL_UNITS if len(u) >= 2) + r')'
    )
    for m in name_val_ref_unit.finditer(text):
        name = m.group(1).strip()
        value = m.group(2)
        unit = m.group(3)
        if name not in existing and name not in new_results:
            new_results[name] = {
                'value': value, 'unit': unit, 'original_name': name
            }

    return new_results


def extract_indicators_from_text(text: str,
                                  template: List[Dict[str, str]] = None,
                                  synonym_map: Dict[str, str] = None) -> Dict[str, Dict]:
    """Extract all measurable indicators from PDF text.

    Uses multi-strategy extraction with two passes:
    Pass 1: structured patterns (brackets, tables, colon format)
    """
    results = {}

    # --- Strategy 0: Blood pressure ---
    bp_pat = re.compile(r'血压\s*[:：]?\s*(\d{2,3})/(\d{2,3})\s*(mmHg)?')
    for m in bp_pat.finditer(text):
        results['血压'] = {
            'value': f"{m.group(1)}/{m.group(2)}",
            'unit': 'mmHg',
            'original_name': '血压'
        }

    # --- Strategy 0b: Value-before-unit (name value unit) ---
    inline_pat = re.compile(
        r'([^\s\n\d]{2,15})\s+(\d+\.?\d*)\s+(' + '|'.join(
            re.escape(u) for u in MEDICAL_UNITS if len(u) >= 2
        ) + r')'
    )
    for m in inline_pat.finditer(text):
        name = m.group(1).strip()
        value = m.group(2)
        unit = m.group(3)
        if name not in results:
            results[name] = {'value': value, 'unit': unit, 'original_name': name}

    # --- Strategy 1: Bracket format 【指标名】值...单位 ---
    bracket_pat = re.compile(
        r'【\*?([^】\n]+)】\s*(\d+\.?\d*(?:/\d+\.?\d*)?)',
    )
    for m in bracket_pat.finditer(text):
        name = m.group(1).strip()
        value = m.group(2)
        rest = text[m.end():m.end() + 30]
        unit = _extract_unit(rest)
        results[name] = {'value': value, 'unit': unit, 'original_name': name}

    # --- Strategy 2: Line-by-line table parsing ---
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line or len(line) < 5:
            continue

        skip_patterns = [
            r'^(项目|序号|检验项|检查项|报告|总结|建议|备注|注意|提示)',
            r'(项目名称|参考范围|参考值|单位|结果)$',
            r'^\d{4}[-/]\d{1,2}[-/]\d{1,2}',
        ]
        if any(re.search(p, line) for p in skip_patterns):
            continue

        unit_result = _find_unit(line)
        if unit_result:
            unit_name, unit_pos = unit_result
            before_unit = line[:unit_pos].strip()
            after_unit = line[unit_pos + len(unit_name):].strip()

            # Strip parenthesized reference ranges from after_unit
            clean_after = re.sub(r'[（(][^）)]*[）)]', '', after_unit).strip()
            value = _extract_numeric_value(clean_after)
            # If clean_after is empty/ref-only, try before_unit (name value ref format)
            if not value and ' ' in before_unit:
                last_parts = before_unit.rsplit(' ', 2)
                if len(last_parts) >= 2:
                    value = _extract_numeric_value(last_parts[-1])
            if not value:
                continue

            name = _extract_name(before_unit)
            if name and len(name) >= 2 and name not in results:
                results[name] = {
                    'value': value, 'unit': unit_name, 'original_name': name
                }
        else:
            ref_match = re.search(
                r'(?<!\d{4})\d{1,3}\.?\d*\s*[－—\-~至到]{1,2}\s*\d+\.?\d*', line
            )
            if not ref_match:
                ref_match = re.search(
                    r'[≤≥<>\u2264\u2265=]+\s*\d+\.?\d*', line
                )
            if ref_match:
                before_ref = line[:ref_match.start()].strip()
                after_ref = line[ref_match.end():].strip()
                value = _extract_numeric_value(after_ref)
                name = _extract_name(before_ref)
                if name and value and len(name) >= 2 and name not in results:
                    results[name] = {
                        'value': value, 'unit': '', 'original_name': name
                    }

    # --- Strategy 3: Colon format 名称：值 ---
    colon_pat = re.compile(r'([^\s\n：:]{2,12})[：:]\s*(\d+\.?\d*)')
    exclude_kw = ['日期', '时间', '科室', '医生', '电话', '单位', '部门', '工号',
                  '结论', '次数', '页码', '编号', '登记号', '档案号']
    for m in colon_pat.finditer(text):
        name = m.group(1).strip()
        if any(kw in name for kw in exclude_kw):
            continue
        value = m.group(2)
        if name not in results:
            results[name] = {
                'value': value, 'unit': '', 'original_name': name
            }

    # --- Second Pass: relaxed scanning for missed items ---
    if template:
        template_canonical = {row['canonical_name'] for row in template}
    else:
        template_canonical = set()

    second_results = _second_pass_scan(text, template_canonical, results)
    # Only add second-pass items if NOT already in first-pass results
    for k, v in second_results.items():
        if k not in results:
            results[k] = v

    # --- Third Pass: join split-line names and re-scan for still-missing items ---
    joined_text = _join_split_names(text)
    if joined_text != text:
        third_results = _second_pass_scan(joined_text, template_canonical, results)
        for k, v in third_results.items():
            if k not in results:
                results[k] = v

    return results


def _find_unit(line: str) -> Optional[Tuple[str, int]]:
    """Find medical unit in a line. Returns (unit, position)."""
    for unit in MEDICAL_UNITS:
        pos = line.find(unit)
        if pos >= 0:
            return (unit, pos)
    return None


def _extract_unit(text: str) -> str:
    """Extract unit from text after a value."""
    for unit in MEDICAL_UNITS:
        if unit in text[:20]:
            return unit
    return ''


def _extract_numeric_value(text: str) -> Optional[str]:
    """Extract numeric value from text, handling various formats."""
    text = text.strip()

    # Blood pressure: 101/59 or 101/59mmHg
    bp_match = re.search(r'(\d+)/(\d+)\s*(?:mmHg)?', text)
    if bp_match:
        return f"{bp_match.group(1)}/{bp_match.group(2)}"

    # Regular number with optional markers
    num_match = re.search(r'(\d+\.?\d*)\s*[↑↓*]?\s*$', text)
    if num_match:
        return num_match.group(1)

    # Any number
    num_match = re.search(r'(\d+\.?\d*)', text)
    if num_match:
        return num_match.group(1)

    return None


def _extract_name(before_ref: str) -> Optional[str]:
    """Extract item name, stripping reference range."""
    cleaned = re.sub(r'^[【】\*\s]+|[【】\*\s:：]+$', '', before_ref.strip())

    # Split at reference range start
    split_pat = re.search(
        r'\s*(?:[：:]\s*\S+|[≤≥<>\u2264\u2265=]+\s*\d+|\d+\.?\d*\s*[－—\-~至到]+|[－—\-]+\s*$)',
        cleaned
    )
    if split_pat:
        name = cleaned[:split_pat.start()].strip()
    else:
        name = cleaned

    name = re.sub(r'[【】\*\s]+', '', name)
    return name if len(name) >= 1 and len(name) <= 20 else None


# ============================================================
# Name Mapping (Hospital → Standard)
# ============================================================

def load_synonym_map() -> Dict[str, str]:
    """Load medical synonym map from references."""
    skill_dir = Path(__file__).parent.parent
    syn_path = skill_dir / "references" / "medical_synonyms.md"

    synonym_map = {}
    if not syn_path.exists():
        return synonym_map

    with open(syn_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Parse lines like: "hospital1, hospital2 → canonical_name"
    for line in content.split('\n'):
        line = line.strip()
        if not line.startswith('-') or '→' not in line:
            continue
        line = line.lstrip('- ').strip()
        parts = line.split('→')
        if len(parts) != 2:
            continue
        variants = [v.strip() for v in parts[0].split(',')]
        canonical = parts[1].strip()
        for v in variants:
            synonym_map[v] = canonical

    return synonym_map


def normalize_indicators(
    raw_indicators: Dict[str, Dict],
    template: List[Dict[str, str]],
    synonym_map: Dict[str, str],
) -> Tuple[Dict[str, Dict], Dict[str, str]]:
    """Map raw PDF indicators to standard names using template + synonyms.

    Returns:
        (normalized_dict, hospital_to_canonical_map)
        normalized_dict: {canonical_name: {value, unit, original_name}}
        hospital_to_canonical_map: {hospital_name: canonical_name}
    """
    template_canonical_set = {row['canonical_name'] for row in template}
    official_feature_set = {row['official_feature_name'] for row in template}
    # Reverse: official_feature_name → canonical_name
    feature_to_canonical = {row['official_feature_name']: row['canonical_name'] for row in template}
    normalized = {}
    hospital_to_canonical = {}

    # Normalize synonym map: values may be canonical_name OR official_feature_name
    # Convert all synonym values to canonical_name
    normalized_synonyms = {}
    for variant, value in synonym_map.items():
        if value in template_canonical_set:
            normalized_synonyms[variant] = value
        elif value in official_feature_set:
            normalized_synonyms[variant] = feature_to_canonical[value]
    synonym_map = normalized_synonyms

    # Build reverse synonym map: canonical → [variants]
    canonical_to_variants: Dict[str, List[str]] = {}
    for variant, canonical in synonym_map.items():
        if canonical not in canonical_to_variants:
            canonical_to_variants[canonical] = []
        canonical_to_variants[canonical].append(variant)

    # Build canonical→feature_name lookup from template
    canonical_to_feature = {
        row['canonical_name']: row['official_feature_name']
        for row in template
    }

    # Handle blood pressure specially
    bp_data = None
    for name, data in list(raw_indicators.items()):
        if '血压' in name and '/' in str(data.get('value', '')):
            bp_value = str(data['value'])
            parts = bp_value.split('/')
            if len(parts) == 2:
                try:
                    v1, v2 = float(parts[0]), float(parts[1])
                    systolic, diastolic = (v1, v2) if v1 > v2 else (v2, v1)
                    bp_data = {
                        'systolic_bp': {'value': str(int(systolic)), 'unit': 'mmHg', 'original_name': name},
                        'diastolic_bp': {'value': str(int(diastolic)), 'unit': 'mmHg', 'original_name': name},
                    }
                    # Store the original PDF term as BP source name.
                    # If the PDF only shows "血压" as one combined field,
                    # both SBP and DBP rows in the mapping file will show "血压".
                    # If the PDF shows "高压"/"低压" separately, those go through
                    # the normal synonym mapping path instead.
                    hospital_to_canonical['_bp_source_name'] = name
                    # Remove raw blood pressure
                    del raw_indicators[name]
                except ValueError:
                    pass
                break

    # Map each raw indicator (store by official_feature_name for data file compatibility)
    for name, data in raw_indicators.items():
        canonical = None

        # Exact match with canonical_name
        if name in template_canonical_set:
            canonical = name
        # Match via synonyms
        elif name in synonym_map and synonym_map[name] in template_canonical_set:
            canonical = synonym_map[name]
        # Fuzzy: name contains a canonical name
        else:
            for cn in sorted(template_canonical_set, key=len, reverse=True):
                if cn in name and len(cn) >= 3:
                    canonical = cn
                    break

        if canonical:
            feat_name = canonical_to_feature.get(canonical, canonical)
            if feat_name not in normalized:
                normalized[feat_name] = data
                if name != canonical:
                    hospital_to_canonical[name] = canonical

    # Add blood pressure data
    if bp_data:
        for feat_name, bp_info in bp_data.items():
            if feat_name not in normalized:
                normalized[feat_name] = bp_info

    return normalized, hospital_to_canonical


# ============================================================
# Output Generation
# ============================================================

def resolve_filename(output_dir: Path, base_name: str) -> Path:
    """Resolve filename conflicts: base → base2 → base3 → ..."""
    name, ext = os.path.splitext(base_name)
    candidate = output_dir / base_name
    if not candidate.exists():
        return candidate

    counter = 2
    while True:
        candidate = output_dir / f"{name}{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def generate_mapping_file(
    hospital_name: str,
    mappings: Dict[str, str],  # hospital_name → canonical_name
    template: List[Dict[str, str]],
    output_dir: Path,
) -> Path:
    """Generate a mapping CSV file for one hospital."""
    filename = f"{hospital_name}mappings.csv"
    filepath = resolve_filename(output_dir, filename)

    with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        # Header
        writer.writerow([
            'hospital_name', 'canonical_name', 'official_feature_name',
            'official_unit', 'official_reference', 'official_group_zh'
        ])

        # Reverse map: canonical → hospital_name
        canonical_to_hospital = {v: k for k, v in mappings.items()}

        # Handle blood pressure: if PDF only showed "血压" as one combined field,
        # both SBP and DBP rows should show the exact original term (e.g. "血压").
        # When separate "高压"/"低压" appear, they go via normal synonym path.
        bp_source = mappings.get('_bp_source_name', '')
        if bp_source:
            canonical_to_hospital['SBP'] = bp_source
            canonical_to_hospital['DBP'] = bp_source

        for row in template:
            cn = row['canonical_name']
            if cn in canonical_to_hospital:
                hn = canonical_to_hospital[cn]
            else:
                hn = ''

            writer.writerow([
                hn,
                cn,
                row['official_feature_name'],
                row['official_unit'],
                row['official_reference'],
                row['official_group_zh'],
            ])

    return filepath


def generate_data_file(
    patients_data: List[Dict[str, Dict]],
    template: List[Dict[str, str]],
    output_dir: Path,
    batch_size: int,
) -> Path:
    """Generate a data CSV file for all patients in the batch."""
    now = datetime.now()
    base_name = f"{str(now.year)[-2:]},{now.month:02d},{now.day:02d} {now.hour:02d}：{now.minute:02d}[{batch_size}].csv"
    filepath = resolve_filename(output_dir, base_name)

    # Feature order from template
    feature_order = [row['official_feature_name'] for row in template]

    with open(filepath, 'w', encoding='utf-8-sig', newline='') as f:
        writer = csv.writer(f)
        # Header row
        writer.writerow(feature_order)

        # Data rows
        for patient in patients_data:
            row_data = []
            for feat in feature_order:
                if feat in patient:
                    val = patient[feat].get('value', '')
                    # For blood pressure (already split), use direct value
                    row_data.append(str(val))
                else:
                    row_data.append('')
            writer.writerow(row_data)

    return filepath


# ============================================================
# Main Processing Pipeline
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description='Medical Report Processor — Process PDF exam reports',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python process.py report.pdf --both
  python process.py report1.pdf report2.pdf --mapping
  python process.py ./reports/ --data
        """
    )
    parser.add_argument('paths', nargs='+', help='PDF file paths or directory paths')
    parser.add_argument('--mapping', action='store_true', help='Output mapping file(s) only')
    parser.add_argument('--data', action='store_true', help='Output data file only')
    parser.add_argument('--both', action='store_true', help='Output both mapping and data files')

    args = parser.parse_args()

    # Determine output mode
    if args.both or (args.mapping and args.data):
        output_mode = 'both'
    elif args.mapping:
        output_mode = 'mapping'
    elif args.data:
        output_mode = 'data'
    else:
        output_mode = 'both'

    # Collect PDFs
    pdf_files = collect_pdf_files(args.paths)
    if not pdf_files:
        print("ERROR: No PDF files found.")
        sys.exit(1)

    print(f"Found {len(pdf_files)} PDF file(s)")
    print(f"Output mode: {output_mode}")
    print(f"{'='*50}")

    # Load template
    template = load_mappings_template()
    synonym_map = load_synonym_map()

    # Output directory = first PDF's folder
    output_dir = pdf_files[0].parent

    # Process each PDF
    hospital_data: Dict[str, List[Tuple[str, Dict[str, Dict]]]] = {}
    all_patients_normalized = []

    for i, pdf_path in enumerate(pdf_files):
        print(f"\n[{i+1}/{len(pdf_files)}] Processing: {pdf_path.name}")

        # Read PDF
        text = read_pdf_text(pdf_path)
        if not text or len(text.strip()) < 50:
            print(f"  [SKIP] Insufficient text content")
            continue

        # Detect hospital
        hospital = detect_hospital_name(text)
        print(f"  Hospital: {hospital}")

        # Extract patient info
        patient_info = extract_patient_info(text)
        print(f"  Patient: {patient_info.get('sample_name', '?')} | "
              f"{patient_info.get('sex', '?')} | "
              f"{patient_info.get('age', '?')}y")

        # Extract indicators
        raw_indicators = extract_indicators_from_text(text, template, synonym_map)
        print(f"  Raw indicators extracted: {len(raw_indicators)}")

        normalized, h2c = normalize_indicators(raw_indicators, template, synonym_map)

        # Merge patient info into normalized
        for key, value in patient_info.items():
            if key in {row['canonical_name'] for row in template}:
                normalized[key] = {
                    'value': value,
                    'unit': '',
                    'original_name': key
                }

        print(f"  Mapped indicators: {len(normalized)}")

        # Store by hospital
        if hospital not in hospital_data:
            hospital_data[hospital] = []
        hospital_data[hospital].append((pdf_path.name, normalized))

        all_patients_normalized.append(normalized)

    if not all_patients_normalized:
        print("\nERROR: No reports successfully processed.")
        sys.exit(1)

    # --- Generate outputs ---
    print(f"\n{'='*50}")
    print(f"Output directory: {output_dir}")

    # Mapping files
    mapping_paths = []
    if output_mode in ('mapping', 'both'):
        print("\n--- Mapping Files ---")
        for hospital, patients_list in hospital_data.items():
            # Build combined mapping for this hospital (union of all patients)
            combined_h2c = {}
            for _, normalized in patients_list:
                for feat_name, data in normalized.items():
                    original = data.get('original_name', feat_name)
                    if original != feat_name:
                        combined_h2c[original] = feat_name

            path = generate_mapping_file(hospital, combined_h2c, template, output_dir)
            mapping_paths.append(path)
            print(f"  ✓ {path.name}")

    # Data file
    data_path = None
    if output_mode in ('data', 'both'):
        print("\n--- Data File ---")
        data_path = generate_data_file(
            all_patients_normalized, template, output_dir, len(pdf_files)
        )
        print(f"  ✓ {data_path.name}")

    print(f"\n{'='*50}")
    print(f"COMPLETE: {len(all_patients_normalized)} patient(s) processed")

    # Return paths for caller
    return {
        'mapping_paths': mapping_paths,
        'data_path': data_path,
        'output_dir': str(output_dir),
    }


if __name__ == "__main__":
    main()
