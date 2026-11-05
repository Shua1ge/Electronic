import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from page_orientation import rotate_box, rotation_from_lines


class OrientationTests(unittest.TestCase):
    def test_dominant_clockwise_text_gets_counterclockwise_correction(self):
        rows=[{'text':'long text label','baseline_degrees':-90,'confidence':1} for _ in range(9)]
        rows.append({'text':'legend','baseline_degrees':0,'confidence':1})
        self.assertEqual(rotation_from_lines(rows),90)
        mixed=rows[:1]+[{'text':'long text label','baseline_degrees':0,'confidence':1}]
        self.assertEqual(rotation_from_lines(mixed),0)

    def test_box_round_trip_and_title_block_location(self):
        box=[.05,.6,.15,.9]
        corrected=rotate_box(box,90)
        self.assertEqual(corrected,[.6,.85,.9,.95])
        for a,b in zip(rotate_box(corrected,270),box):self.assertAlmostEqual(a,b)


if __name__=='__main__':unittest.main()
