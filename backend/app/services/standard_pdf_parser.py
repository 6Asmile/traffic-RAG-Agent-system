import gc
import os
import re
import tempfile
from typing import Any

from docling.document_converter import DocumentConverter
from pypdf import PdfReader, PdfWriter


class StandardPdfParser:
    STANDARD_NAME_RE = re.compile(
        r"(?i)(?:^|[\s_])(GB(?:/T)?\s*[-+]?\s*\d+(?:\.\d+)*(?:-\d{4})?)"
    )
    STANDARD_HINT_RE = re.compile(
        r"(国家标准|标准|规范|规程|技术要求|技术条件|道路交通标志|道路交通标线)"
    )
    CHAPTER_RE = re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+章\b")
    SECTION_RE = re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+节\b")
    ARTICLE_RE = re.compile(r"^\s*第[一二三四五六七八九十百千万0-9]+条\b")
    APPENDIX_RE = re.compile(r"^\s*附录\s*[A-ZＡ-Ｚ一二三四五六七八九十]?\b")
    TABLE_RE = re.compile(r"^\s*表\s*[0-9A-Za-z一二三四五六七八九十\.\-]+")
    FIGURE_RE = re.compile(r"^\s*图\s*[0-9A-Za-z一二三四五六七八九十\.\-]+")

    @classmethod
    def looks_like_standard_pdf(cls, file_path: str) -> bool:
        name = os.path.basename(str(file_path or ""))
        stem = os.path.splitext(name)[0]
        if cls.STANDARD_NAME_RE.search(stem):
            return True
        return bool(cls.STANDARD_HINT_RE.search(stem))

    @classmethod
    def parse_docling_batched(
        cls,
        file_path: str,
        batch_size: int = 5,
    ) -> tuple[str, dict[str, Any]]:
        converter = DocumentConverter()
        reader = PdfReader(file_path)
        total_pages = len(reader.pages)
        if total_pages <= 0:
            raise ValueError("PDF 页数为 0，无法解析")

        markdown_parts: list[str] = []
        batch_profiles: list[dict[str, Any]] = []

        print(f"📄 PDF 共 {total_pages} 页，Docling 分批解析中...")
        for i in range(0, total_pages, batch_size):
            writer = PdfWriter()
            start_page = i + 1
            end_page = min(i + batch_size, total_pages)
            for j in range(i, end_page):
                writer.add_page(reader.pages[j])

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                writer.write(tmp.name)
                tmp_path = tmp.name

            try:
                result = converter.convert(tmp_path)
                markdown = str(result.document.export_to_markdown() or "").strip()
                markdown_parts.append(markdown)
                batch_profiles.append(
                    cls._build_batch_profile(
                        markdown=markdown,
                        batch_index=(i // batch_size) + 1,
                        start_page=start_page,
                        end_page=end_page,
                    )
                )
                print(f"   ⏳ 进度：已解析 {end_page} / {total_pages} 页")
                del result
                gc.collect()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        merged_markdown = "\n\n".join(part for part in markdown_parts if part).strip()
        normalized_markdown, doc_meta = cls._normalize_standard_markdown(
            merged_markdown,
            os.path.basename(file_path),
        )
        parse_meta = {
            "route": "pdf_standard_docling_batched_markdown",
            "doc_profile": "standard_spec",
            "total_pages": int(total_pages),
            "batch_size": int(batch_size),
            "batch_profiles": batch_profiles[:80],
            **doc_meta,
        }
        return normalized_markdown, parse_meta

    @classmethod
    def _build_batch_profile(
        cls,
        markdown: str,
        batch_index: int,
        start_page: int,
        end_page: int,
    ) -> dict[str, Any]:
        text = str(markdown or "")
        line_count = len([line for line in text.splitlines() if line.strip()])
        has_table = bool(re.search(r"\|.+\|", text) and re.search(r"\|\s*-{2,}\s*\|", text))
        has_figure = bool(cls.FIGURE_RE.search(text) or re.search(r"!\[.*?\]\(.*?\)", text))
        has_article = bool(cls.ARTICLE_RE.search(text))
        if has_table and has_figure:
            page_type = "mixed"
        elif has_table:
            page_type = "table"
        elif has_figure:
            page_type = "figure"
        elif has_article:
            page_type = "article"
        else:
            page_type = "text"
        return {
            "batch": int(batch_index),
            "pages": f"{start_page}-{end_page}",
            "page_type": page_type,
            "line_count": int(line_count),
            "table_hits": len(re.findall(r"\|.+\|", text)),
            "figure_hits": len(cls.FIGURE_RE.findall(text)),
            "article_hits": len(cls.ARTICLE_RE.findall(text)),
        }

    @classmethod
    def _normalize_standard_markdown(
        cls,
        markdown: str,
        filename: str,
    ) -> tuple[str, dict[str, Any]]:
        raw = str(markdown or "").replace("\r\n", "\n").strip()
        if not raw:
            raise ValueError("Docling 未提取到有效文本")

        standard_code = cls._extract_standard_code(f"{filename}\n{raw[:2000]}")
        title = cls._extract_title(raw, filename)
        lines = cls._merge_structural_blocks(raw.splitlines())
        normalized_lines: list[str] = []

        if standard_code:
            normalized_lines.append(f"# {standard_code}")
        if title:
            normalized_lines.append(f"## {title}")

        last_heading = ""
        for line in lines:
            current = str(line or "").strip()
            if not current:
                normalized_lines.append("")
                continue
            if current.startswith("#"):
                normalized_lines.append(current)
                last_heading = current
                continue
            if cls.CHAPTER_RE.match(current):
                current = f"## {current}"
                last_heading = current
            elif cls.SECTION_RE.match(current):
                current = f"### {current}"
                last_heading = current
            elif cls.ARTICLE_RE.match(current):
                current = f"#### {current}"
                last_heading = current
            elif cls.APPENDIX_RE.match(current):
                current = f"## {current}"
                last_heading = current
            elif cls.TABLE_RE.match(current):
                current = f"##### {current}"
                last_heading = current
            elif cls.FIGURE_RE.match(current):
                current = f"##### {current}"
                last_heading = current
            elif standard_code and current == standard_code:
                continue
            elif title and current == title:
                continue
            normalized_lines.append(current)

        normalized = "\n".join(normalized_lines).strip()
        doc_meta = {
            "standard_code": standard_code,
            "standard_title": title,
            "chapter_hits": len(cls.CHAPTER_RE.findall(normalized)),
            "section_hits": len(cls.SECTION_RE.findall(normalized)),
            "article_hits": len(cls.ARTICLE_RE.findall(normalized)),
            "appendix_hits": len(cls.APPENDIX_RE.findall(normalized)),
            "table_caption_hits": len(cls.TABLE_RE.findall(normalized)),
            "figure_caption_hits": len(cls.FIGURE_RE.findall(normalized)),
        }
        return normalized, doc_meta

    @classmethod
    def _merge_structural_blocks(cls, lines: list[str]) -> list[str]:
        merged: list[str] = []
        idx = 0
        total = len(lines)

        while idx < total:
            current = str(lines[idx] or "").strip()
            if not current:
                merged.append("")
                idx += 1
                continue

            if cls.TABLE_RE.match(current):
                block = [current]
                idx += 1
                while idx < total:
                    next_line = str(lines[idx] or "").rstrip()
                    next_trimmed = next_line.strip()
                    if not next_trimmed:
                        if block and block[-1] != "":
                            block.append("")
                        idx += 1
                        continue
                    if cls._is_hard_boundary(next_trimmed):
                        break
                    # 表格后紧跟 markdown table、表注、说明时一并绑定
                    if "|" in next_trimmed or next_trimmed.startswith(":") or next_trimmed.startswith("注"):
                        block.append(next_trimmed)
                        idx += 1
                        continue
                    if len(next_trimmed) <= 40:
                        block.append(next_trimmed)
                        idx += 1
                        continue
                    break
                merged.append("\n".join([line for line in block if line is not None]).strip())
                continue

            if cls.FIGURE_RE.match(current):
                block = [current]
                idx += 1
                while idx < total:
                    next_line = str(lines[idx] or "").rstrip()
                    next_trimmed = next_line.strip()
                    if not next_trimmed:
                        if block and block[-1] != "":
                            block.append("")
                        idx += 1
                        continue
                    if cls._is_hard_boundary(next_trimmed):
                        break
                    # 图题后通常跟短说明、图注、限制值说明
                    if len(next_trimmed) <= 80:
                        block.append(next_trimmed)
                        idx += 1
                        continue
                    break
                merged.append("\n".join([line for line in block if line is not None]).strip())
                continue

            merged.append(current)
            idx += 1

        return merged

    @classmethod
    def _is_hard_boundary(cls, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if raw.startswith("#"):
            return True
        if cls.CHAPTER_RE.match(raw):
            return True
        if cls.SECTION_RE.match(raw):
            return True
        if cls.ARTICLE_RE.match(raw):
            return True
        if cls.APPENDIX_RE.match(raw):
            return True
        if cls.TABLE_RE.match(raw):
            return True
        if cls.FIGURE_RE.match(raw):
            return True
        return False

    @classmethod
    def _extract_standard_code(cls, text: str) -> str:
        match = cls.STANDARD_NAME_RE.search(str(text or ""))
        if not match:
            return ""
        return re.sub(r"\s+", "", match.group(1)).replace("+", " ")

    @classmethod
    def _extract_title(cls, markdown: str, filename: str) -> str:
        candidates = []
        for line in str(markdown or "").splitlines()[:40]:
            current = str(line or "").strip().strip("#").strip()
            if not current:
                continue
            if cls.STANDARD_NAME_RE.search(current):
                continue
            if len(current) < 6:
                continue
            if current.count("|") >= 2:
                continue
            candidates.append(current)
        if candidates:
            return candidates[0]
        return os.path.splitext(os.path.basename(filename))[0]
