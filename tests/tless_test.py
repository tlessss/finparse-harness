import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "."))

from src.engine_orchestrator import FinParseAI

# 定位项目根目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
pdf_path = os.path.join(_PROJECT_ROOT, "pdfs", "多氟多-2025.pdf")

result = FinParseAI().run(pdf_path, stock_code="002407", report_year=2025)