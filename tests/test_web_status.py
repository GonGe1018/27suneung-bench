import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from suneung_benchmark.cost_pilot import exam_parts
from suneung_benchmark.web_status import build_snapshot


class PublicExamStatusTest(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = {"exam_year": 2027, "models": [
            {"model": "gpt-6.2-astra", "scope": "core", "provider": "openai", "effort": "max"},
        ]}
        self.plan = [{"id": "later", "scope": "all", "name": "Later", "provider": "Example"}]

    def snapshot(self, previous=None, **options):
        return build_snapshot(self.config, self.plan, self.root, self.root, previous or {}, **options)

    def record(self, parts):
        fields = ("model", "part", "status", "max_points", "correct", "answered",
                  "question_count", "input_sha256", "estimated_usd")
        with (self.root / "costs.csv").open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fields)
            writer.writeheader()
            for part in parts:
                writer.writerow(dict(model="gpt-6.2-astra", part=part.name, status="completed",
                                     max_points=100, correct=part.question_count, answered=part.question_count,
                                     question_count=part.question_count, input_sha256="abc", estimated_usd=0.5))

    def test_release_shows_running_without_partial_scores(self):
        self.assertEqual(self.snapshot()["state"], "awaiting")
        (self.root / "math-exam.pdf").touch()
        self.assertEqual(self.snapshot()["state"], "running")
        self.record(exam_parts()[:1])
        running = self.snapshot()
        self.assertEqual(running["state"], "running")
        self.assertFalse(running["models"]["gpt-6.2-astra"]["parts"])
        self.assertEqual(running["completion"], {
            "plannedBundles": 33,
            "scoredBundles": 1,
            "publishedBundles": 0,
        })

    def test_area_progress_counts_ready_papers_and_fully_scored_models(self):
        math = [part for part in exam_parts() if part.subject == "math"]
        (self.root / f"{math[0].name}-exam.pdf").touch()
        self.assertEqual(self.snapshot()["progress"]["math"], {
            "readyParts": 1,
            "totalParts": len(math),
            "scoredModels": 0,
            "plannedModels": 2,
        })
        self.record(math[:-1])
        self.assertEqual(self.snapshot()["progress"]["math"]["scoredModels"], 0)
        self.record(math)
        snapshot = self.snapshot()
        self.assertEqual(snapshot["state"], "running")
        self.assertEqual(snapshot["progress"]["math"], {
            "readyParts": len(math),
            "totalParts": len(math),
            "scoredModels": 1,
            "plannedModels": 2,
        })
        self.assertEqual(snapshot["progress"]["english"]["readyParts"], 0)
        self.assertEqual(snapshot["models"]["gpt-6.2-astra"]["parts"], {})

    def test_complete_publishes_scores_and_keeps_deferred_models(self):
        core = [p for p in exam_parts() if p.subject in {"korean", "math", "english"}]
        self.record(core)
        full = self.snapshot()
        self.assertEqual(full["state"], "complete")
        self.assertEqual(len(full["models"]["gpt-6.2-astra"]["parts"]), len(core))
        self.assertEqual(len(full["models"]["later"]["expectedParts"]), 25)
        self.assertEqual(full["models"]["later"]["parts"], {})
        self.assertEqual(full["completion"], {
            "plannedBundles": 33,
            "scoredBundles": len(core),
            "publishedBundles": len(core),
        })
        (self.root / "costs.csv").unlink()
        self.assertEqual(self.snapshot(previous=full), full)

    def test_early_close_requires_scores(self):
        with self.assertRaises(ValueError):
            self.snapshot(finalize=True)
        self.record(exam_parts()[:1])
        partial = self.snapshot(finalize=True)
        self.assertEqual(partial["state"], "complete")
        self.assertEqual(partial["completion"]["scoredBundles"], 1)
        self.assertEqual(partial["completion"]["publishedBundles"], 1)
