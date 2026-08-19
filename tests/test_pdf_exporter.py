import io
import sys
import unittest
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
sys.path.insert(0, str(WEB))

try:
    import pdfplumber
    from pdf_exporter import render_document_pdf
except ImportError:
    pdfplumber = None


@unittest.skipUnless(pdfplumber, "PDF dependencies are installed in the runtime image")
class PdfExporterTests(unittest.TestCase):
    def test_chinese_resume_pdf_is_valid_and_extractable(self):
        content = "# 张三 · 个人简历\n\n**求职意向：** 游戏策划\n\n## 项目经历\n\n- CareerPilot 求职助手"
        payload = render_document_pdf(content, "张三个人简历")
        self.assertTrue(payload.startswith(b"%PDF"))
        self.assertGreater(len(payload), 1000)
        with pdfplumber.open(io.BytesIO(payload)) as document:
            self.assertEqual(len(document.pages), 1)
            self.assertIn("CareerPilot", document.pages[0].extract_text())


if __name__ == "__main__":
    unittest.main()
