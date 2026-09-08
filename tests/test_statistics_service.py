# audience: internal
# # statistics-service-tests
"""真实内容读取验证预览隔离、续读去重及统计失败时的正文交付."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from harness_manager.protocol import document_identity, validate_document
from harness_manager.service import Manager


# //// 创建有独立来源的可读取 Skill [@x380kkm 2026-09-07] ////
class StatisticsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "SKILL.md"
        source.write_text("---\nname: sample\ndescription: Read a complete method.\n---\n# Method\nRead the inputs.\n", encoding="utf-8")
        self.manager = Manager(read_roots=[root], user_root=root)
        document = self.manager.import_file(str(source))["document"]
        self.manager.apply_document(self.manager.preview_document(document)["plan"])
        self.manager.apply_document(self.manager.preview_usage(document_identity(document), {"state": "enabled"})["plan"])
        self.ref = document["id"] + "#sample"

    # //// 完整预览与跨调用续读均保留预览用途 [@x380kkm 2026-09-07] ////
    def test_preview_and_preview_continuation_do_not_count(self) -> None:
        self.manager.preview_content(self.ref, "local")
        partial = self.manager.preview_content(self.ref, "local", budget=1)
        self.manager.continue_content(partial["continuation"])
        self.assertEqual(self.manager.read_statistics()["total"], 0)
        self.assertFalse(self.manager.statistics.path.exists())

    # //// 同一读取快照经重试完成时只计一次 [@x380kkm 2026-09-07] ////
    def test_agent_continuation_retries_count_once(self) -> None:
        partial = self.manager.open_content(self.ref, "local", budget=1)
        self.assertEqual(self.manager.read_statistics()["total"], 0)
        complete = self.manager.continue_content(partial["continuation"])
        self.manager.continue_content(partial["continuation"])
        self.assertEqual(complete["readiness"], "ready")
        self.assertEqual(self.manager.read_statistics()["total"], 1)
        self.manager.open_content(self.ref, "local")
        self.assertEqual(self.manager.read_statistics()["total"], 2)

    # //// 统计存储失败仍交付完整方法并给出具体诊断 [@x380kkm 2026-09-07] ////
    def test_statistics_failure_preserves_content_delivery(self) -> None:
        with patch.object(self.manager.statistics, "record", side_effect=OSError("offline")):
            result = self.manager.open_content(self.ref, "local")
        self.assertEqual(result["readiness"], "ready")
        self.assertTrue(any(item["code"] == "statistics-unavailable" for item in result["diagnostics"]))
        validate_document(result)


if __name__ == "__main__":
    unittest.main()
