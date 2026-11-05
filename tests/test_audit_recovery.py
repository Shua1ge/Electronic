import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"scripts"))
from audit_document import recover_complete_records


class RecoveryTests(unittest.TestCase):
    def test_truncated_last_record_not_invented(self):
        raw='```json\n{"records":[{"evidence":"contains } and [ symbols","fields":{"芯数":72}},{"fields":{"芯数":'
        rows=recover_complete_records(raw)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["fields"]["芯数"],72)

    def test_no_records_no_recovery(self):
        self.assertEqual(recover_complete_records('I could not read the image'),[])


if __name__=="__main__":
    unittest.main()
