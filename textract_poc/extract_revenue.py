#!/usr/bin/env python3
"""
Textract PoC: Extract the Revenue value from the 'STATEMENT OF PROFIT OR LOSS' table.

Usage:
  python extract_revenue.py --pdf path/to/Boxer.pdf --target-year 2024

Environment:
  Requires AWS credentials configured in the environment (e.g., via AWS CLI or env vars).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional, Tuple

import boto3


def read_file_bytes(pdf_path: str) -> bytes:
    if not os.path.isfile(pdf_path):
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    with open(pdf_path, "rb") as f:
        return f.read()


def build_block_index(blocks: List[Dict]) -> Tuple[Dict[str, Dict], Dict[str, List[str]]]:
    """Index blocks by Id and their relationships.

    Returns:
        id_to_block: BlockId -> block
        block_children: BlockId -> list of child BlockIds (if any)
    """
    id_to_block: Dict[str, Dict] = {}
    block_children: Dict[str, List[str]] = {}

    for block in blocks:
        block_id = block.get("Id")
        id_to_block[block_id] = block
        child_ids: List[str] = []
        for rel in block.get("Relationships", []) or []:
            if rel.get("Type") in {"CHILD", "MERGED_CELL"}:
                child_ids.extend(rel.get("Ids", []) or [])
        if child_ids:
            block_children[block_id] = child_ids
    return id_to_block, block_children


def extract_text_from_block(block: Dict, id_to_block: Dict[str, Dict]) -> str:
    """Concatenate text from LINE or CELL by walking WORD children."""
    if block.get("BlockType") in {"WORD"}:
        return block.get("Text", "")
    text_fragments: List[str] = []
    for rel in block.get("Relationships", []) or []:
        if rel.get("Type") != "CHILD":
            continue
        for child_id in rel.get("Ids", []) or []:
            child = id_to_block.get(child_id)
            if not child:
                continue
            if child.get("BlockType") == "WORD":
                token = child.get("Text", "")
                if token:
                    text_fragments.append(token)
            elif child.get("BlockType") == "SELECTION_ELEMENT":
                # Could append "X" for selected checkboxes; not required here
                pass
    return " ".join(text_fragments).strip()


def page_contains_phrase(blocks: List[Dict], id_to_block: Dict[str, Dict], page_number: int, phrase: str) -> bool:
    phrase_lower = phrase.lower()
    for block in blocks:
        if block.get("Page") != page_number:
            continue
        if block.get("BlockType") in {"LINE", "CELL", "TABLE"}:
            text = extract_text_from_block(block, id_to_block)
            if phrase_lower in (text or "").lower():
                return True
    return False


def get_tables_on_page(blocks: List[Dict], page_number: int) -> List[Dict]:
    return [b for b in blocks if b.get("BlockType") == "TABLE" and b.get("Page") == page_number]


def build_table_matrix(table_block: Dict, id_to_block: Dict[str, Dict]) -> List[List[str]]:
    """Build a 2D matrix of cell texts indexed by (row_index-1, col_index-1).

    Textract CELL blocks have RowIndex/ColumnIndex starting at 1.
    Some cells may be merged; we place text in the top-left position.
    """
    cells: List[Dict] = []
    for rel in table_block.get("Relationships", []) or []:
        if rel.get("Type") != "CHILD":
            continue
        for child_id in rel.get("Ids", []) or []:
            child = id_to_block.get(child_id)
            if child and child.get("BlockType") == "CELL":
                cells.append(child)

    max_row = 0
    max_col = 0
    for cell in cells:
        max_row = max(max_row, cell.get("RowIndex", 0))
        max_col = max(max_col, cell.get("ColumnIndex", 0))

    matrix: List[List[str]] = [["" for _ in range(max_col)] for _ in range(max_row)]

    for cell in cells:
        r = (cell.get("RowIndex", 1) - 1)
        c = (cell.get("ColumnIndex", 1) - 1)
        text = extract_text_from_block(cell, id_to_block)
        if r < len(matrix) and c < len(matrix[r]) and text:
            matrix[r][c] = text

    return matrix


def normalize(s: str) -> str:
    return (s or "").strip().lower()


def find_column_index_by_header(table: List[List[str]], header_query: str) -> Optional[int]:
    target = normalize(header_query)
    if not table:
        return None
    # Try first two rows for headers
    header_rows = table[:2]
    for row in header_rows:
        for idx, value in enumerate(row):
            if normalize(value) == target or target in normalize(value):
                return idx
    return None


def find_row_index_by_label(table: List[List[str]], label: str) -> Optional[int]:
    target = normalize(label)
    for r_idx, row in enumerate(table):
        if not row:
            continue
        if normalize(row[0]) == target or target in normalize(row[0]):
            return r_idx
    return None


def analyze_pdf_and_extract(pdf_bytes: bytes, target_year: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Run Textract AnalyzeDocument on given PDF bytes and extract Revenue value.

    Returns:
        value, debug_info
    """
    client = boto3.client("textract")

    response = client.analyze_document(
        Document={"Bytes": pdf_bytes},
        FeatureTypes=["TABLES", "FORMS"],
    )

    blocks: List[Dict] = response.get("Blocks", []) or []
    if not blocks:
        return None, "No blocks returned"

    id_to_block, _ = build_block_index(blocks)

    # Detect pages
    pages = sorted({b.get("Page") for b in blocks if b.get("Page") is not None})
    target_page = None
    for page in pages:
        if page_contains_phrase(blocks, id_to_block, page, "STATEMENT OF PROFIT OR LOSS"):
            target_page = page
            break

    if target_page is None:
        return None, "Could not find page containing 'STATEMENT OF PROFIT OR LOSS'"

    tables = get_tables_on_page(blocks, target_page)
    if not tables:
        return None, "No tables found on target page"

    # For each table, build matrix and attempt extraction
    for table_block in tables:
        matrix = build_table_matrix(table_block, id_to_block)
        if not matrix or not matrix[0]:
            continue

        # Determine column for the target year
        year_col_idx: Optional[int] = None
        if target_year:
            year_col_idx = find_column_index_by_header(matrix, str(target_year))

        # Fallback to the second column if not found
        if year_col_idx is None and len(matrix[0]) >= 2:
            year_col_idx = 1

        # Find Revenue row
        revenue_row_idx = find_row_index_by_label(matrix, "Revenue")
        if revenue_row_idx is None or year_col_idx is None:
            continue

        row = matrix[revenue_row_idx]
        if year_col_idx < len(row):
            value = row[year_col_idx].strip()
            if value:
                return value, f"page={target_page}, table_extracted_rows={len(matrix)} cols={len(matrix[0])}"

    return None, "Revenue not found in tables on target page"


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract Revenue from 'STATEMENT OF PROFIT OR LOSS' using Textract")
    parser.add_argument("--pdf", required=True, help="Path to the Boxer PDF")
    parser.add_argument("--target-year", required=False, help="Target year column header, e.g., 2024")
    args = parser.parse_args()

    try:
        pdf_bytes = read_file_bytes(args.pdf)
    except Exception as e:
        print(f"Error reading PDF: {e}", file=sys.stderr)
        sys.exit(1)

    value, debug = analyze_pdf_and_extract(pdf_bytes, args.target_year)
    if value:
        print(f"Extracted Revenue: {value}")
        if debug:
            print(f"Debug: {debug}")
        sys.exit(0)
    else:
        print("Failed to extract Revenue.", file=sys.stderr)
        if debug:
            print(f"Reason: {debug}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()

