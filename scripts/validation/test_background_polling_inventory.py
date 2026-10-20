"""Host-safe inventory gate: new timer-bearing owners require a stated purpose."""
import io
import json
from pathlib import Path
import re
import tokenize
import unittest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "scripts/validation/background_polling_inventory.json"
PYTHON_TIMER = re.compile(r"\basyncio\s*\.\s*(?:sleep|wait_for|wait)\s*\(")
FRONTEND_TIMER = re.compile(r"\b(?:setInterval|setTimeout)\s*\(")


def python_timer_count(source):
    # Tokenization works on host Python versions predating application type
    # parameter syntax; strings and comments cannot create inventory entries.
    tokens = [(kind, value if kind not in (tokenize.STRING, tokenize.COMMENT) else "")
              for kind, value, *_ in tokenize.generate_tokens(io.StringIO(source).readline)]
    return len(PYTHON_TIMER.findall(tokenize.untokenize(tokens)))


def timer_owners(root):
    owners = {}
    for path in sorted((root / "backend/app").rglob("*.py")):
        count = python_timer_count(path.read_text())
        if count:
            owners[str(path.relative_to(root))] = count
    for path in sorted((root / "frontend/src").rglob("*")):
        if path.suffix not in (".ts", ".tsx") or any(tag in path.name for tag in (".test.", ".spec.")):
            continue
        count = len(FRONTEND_TIMER.findall(path.read_text()))
        if count:
            owners[str(path.relative_to(root))] = count
    return owners


class BackgroundPollingInventoryTests(unittest.TestCase):
    def test_all_timer_bearing_owners_have_reviewed_purpose_and_current_site_counts(self):
        manifest = json.loads(MANIFEST.read_text())
        self.assertEqual(manifest["healthy_repair_seconds"], 60)
        entries = manifest["owners"]
        self.assertEqual(len(entries), len({entry["path"] for entry in entries}))
        self.assertEqual({entry["path"]: entry["timer_sites"] for entry in entries}, timer_owners(ROOT))
        for entry in entries:
            with self.subTest(path=entry["path"]):
                self.assertIn(entry["classification"], {"durable_work", "physical_state", "transport_authority", "bounded_request", "display_interaction", "mixed"})
                self.assertGreaterEqual(len(entry["purpose"]), 30)
                self.assertGreater(entry["timer_sites"], 0)
        retained = {entry["path"] for entry in entries if entry["classification"] == "physical_state"}
        self.assertIn("backend/app/services/access_devices.py", retained)
        self.assertIn("backend/app/services/gate_malfunctions.py", retained)


if __name__ == "__main__":
    unittest.main()
