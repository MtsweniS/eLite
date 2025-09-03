## Textract PoC: Extract Revenue from Statement of Profit or Loss

This PoC uses AWS Textract to parse a Boxer PDF and extract the Revenue value from the table on the page containing the heading "STATEMENT OF PROFIT OR LOSS".

### Prerequisites
- Python 3.9+
- AWS credentials configured (via `aws configure`, environment variables, or instance role)

### Install
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Usage
```bash
python extract_revenue.py --pdf path/to/Boxer.pdf --target-year 2024
```
- `--target-year` is optional. If omitted, the script will try the second column by default.

### Output Example
```
Extracted Revenue: R 15,382,249,000
Debug: page=3, table_extracted_rows=20 cols=5
```

### Notes
- The script uses Textract's `AnalyzeDocument` with `TABLES` and `FORMS` features to build table structures, then searches for the table that contains a "Revenue" row on the target page.
- If the exact column header is not found, it falls back to column index 2.
