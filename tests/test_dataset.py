"""Behavioral tests mutate disposable OOXML copies, never the input workbook."""
from dataclasses import FrozenInstanceError
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile

from scheduler import DatasetError, load_dataset
from scheduler.dataset import NS

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(os.environ.get('SCHEDULER_TEST_DATASET', str(ROOT / 'data/UniversityDataset_Corrected.xlsx')))


class DatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (ROOT / 'outputs').mkdir(exist_ok=True)
        cls.original = SOURCE.read_bytes()
        cls.dataset = load_dataset(SOURCE)

    def changed(self, member, edit):
        temp = tempfile.TemporaryDirectory(dir=ROOT / 'outputs')
        self.addCleanup(temp.cleanup)
        dest = Path(temp.name) / 'input.xlsx'
        with zipfile.ZipFile(SOURCE) as source, zipfile.ZipFile(dest, 'w') as output:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename == member:
                    tree = ET.fromstring(data)
                    edit(tree)
                    data = ET.tostring(tree)
                output.writestr(item, data)
        return dest

    def cell(self, sheet_number, ref, value, typ='n'):
        def edit(tree):
            cell = tree.find(f'.//s:c[@r="{ref}"]', NS)
            cell.clear()
            cell.set('r', ref)
            cell.set('t', typ)
            if value is not None:
                ET.SubElement(cell, '{' + NS['s'] + '}v').text = str(value)
        return self.changed(f'xl/worksheets/sheet{sheet_number}.xml', edit)

    def assert_issue(self, path, sheet, cell, text):
        with self.assertRaises(DatasetError) as context:
            load_dataset(path)
        self.assertTrue(any(e.sheet == sheet and e.cell == cell and text in e.message for e in context.exception.issues), str(context.exception))

    def test_real_data_counts_and_unchanged_bytes(self):
        self.assertEqual(len(self.dataset.tables), 14)
        self.assertEqual(len(self.dataset.courses), 120)
        self.assertEqual(len(self.dataset.students), 200)
        self.assertEqual(len(self.dataset.tables['StudentRequests']), 1695)
        self.assertEqual(len(self.dataset.warnings), 0)
        self.assertEqual(SOURCE.read_bytes(), self.original)
        self.assertEqual(self.dataset.sha256, hashlib.sha256(self.original).hexdigest())

    def test_types_null_zero_and_minutes(self):
        self.assertIs(self.dataset.tables['ProfessorCourses'][0].can_teach, True)
        self.assertIsNone(self.dataset.tables['ProfessorAvailability'][0].weight)
        self.assertTrue(any(p.weight == 0 for p in self.dataset.tables['StudentPreferences']))
        self.assertEqual(self.dataset.tables['TimeSlots'][0].start_time, 480)
        self.assertEqual(self.dataset.tables['Professors'][0].min_minutes, 450)

    def test_sample_feasibility_diagnostics_are_optional(self):
        dataset = load_dataset(SOURCE, include_feasibility_diagnostics=True)
        self.assertEqual(len(dataset.warnings), 40)
        self.assertEqual(dataset.sha256, self.dataset.sha256)

    def test_new_room_type_and_incomplete_resource_sample_are_accepted(self):
        path = self.cell(3, 'I2', 'FutureFacultyLab', 'str')
        dataset = load_dataset(path)
        self.assertEqual(dataset.courses['CE001'].required_room_type, 'FutureFacultyLab')
        self.assertEqual(len(dataset.warnings), 0)
        diagnosed = load_dataset(path, include_feasibility_diagnostics=True)
        self.assertTrue(any(w.sheet == 'Courses' and w.cell == 'I2' and 'no compatible room' in w.message for w in diagnosed.warnings))

    def test_missing_qualified_resources_do_not_block_incomplete_sample(self):
        def edit(tree):
            for cell in tree.findall('.//s:c', NS):
                if cell.get('r', '').startswith('C') and cell.get('t') == 'b':
                    cell.find('s:v', NS).text = '0'
        path = self.changed('xl/worksheets/sheet7.xml', edit)
        self.assertEqual(len(load_dataset(path).warnings), 0)
        diagnosed = load_dataset(path, include_feasibility_diagnostics=True)
        self.assertEqual(sum('no qualified professor' in w.message for w in diagnosed.warnings), 120)

    def test_immutable(self):
        with self.assertRaises(TypeError):
            self.dataset.tables['Courses'] = ()
        with self.assertRaises(FrozenInstanceError):
            self.dataset.courses['CE001'].credits = 99

    def test_boolean_not_accepted_as_number(self):
        self.assert_issue(self.cell(3, 'D2', '1', 'b'), 'Courses', 'D2', 'finite number')

    def test_numeric_boolean_is_rejected(self):
        self.assert_issue(self.cell(7, 'C2', '1'), 'ProfessorCourses', 'C2', 'Boolean')

    def test_fractional_credits(self):
        self.assert_issue(self.cell(3, 'D2', '2.5'), 'Courses', 'D2', 'integer')

    def test_unknown_fk_has_original_coordinate(self):
        self.assert_issue(self.cell(3, 'B2', 'MISSING', 'str'), 'Courses', 'B2', 'unknown reference')

    def test_missing_header(self):
        self.assert_issue(self.cell(3, 'D1', None), 'Courses', 'A1', 'missing')

    def test_duplicate_headers(self):
        self.assert_issue(self.cell(3, 'D1', 'CourseID', 'str'), 'Courses', 'A1', 'duplicate column')

    def test_duplicate_primary_key(self):
        self.assert_issue(self.cell(3, 'A3', 'CE001', 'str'), 'Courses', 'A3', 'duplicate key')

    def test_duplicate_student_request_is_rejected_before_dictionary_conversion(self):
        def duplicate(tree):
            for col in ('A', 'B'):
                first = tree.find(f'.//s:c[@r="{col}2"]', NS)
                second = tree.find(f'.//s:c[@r="{col}3"]', NS)
                second.set('t', first.get('t'))
                second.find('s:v', NS).text = first.find('s:v', NS).text
            tree.find('.//s:c[@r="C2"]/s:v', NS).text = '10'
            tree.find('.//s:c[@r="C3"]/s:v', NS).text = '1'
        path = self.changed('xl/worksheets/sheet12.xml', duplicate)
        self.assert_issue(path, 'StudentRequests', 'A3', 'duplicate student request')
        self.assert_issue(path, 'StudentRequests', 'A3', 'first occurrence at A2')

    def test_missing_sheet(self):
        def remove(tree):
            sheets = tree.find('s:sheets', NS)
            sheets.remove(sheets[0])
        self.assert_issue(self.changed('xl/workbook.xml', remove), 'Faculties', 'A1', 'missing')

    def test_invalid_time(self):
        self.assert_issue(self.cell(5, 'D2', '25:00', 'str'), 'TimeSlots', 'D2', 'HH:MM')

    def test_formula_cache_is_not_trusted(self):
        def add_formula(tree):
            cell = tree.find('.//s:c[@r="D2"]', NS)
            ET.SubElement(cell, '{' + NS['s'] + '}f').text = '1+2'
        self.assert_issue(self.changed('xl/worksheets/sheet3.xml', add_formula), 'Courses', 'D2', 'formulas')

    def test_blank_preferred_weight(self):
        row = next(i + 2 for i, r in enumerate(self.dataset.tables['ProfessorAvailability']) if r.status == 'Preferred')
        self.assert_issue(self.cell(8, f'E{row}', None), 'ProfessorAvailability', f'E{row}', 'requires a weight')

    def test_zero_weight_is_not_blank(self):
        row = next(i + 2 for i, r in enumerate(self.dataset.tables['ProfessorAvailability']) if r.status == 'Preferred')
        dataset = load_dataset(self.cell(8, f'E{row}', 0))
        self.assertEqual(dataset.tables['ProfessorAvailability'][row - 2].weight, 0)

    def test_empty_physical_rows_preserve_error_location(self):
        def edit(tree):
            rows = tree.find('s:sheetData', NS)
            for row in rows:
                if int(row.get('r')) >= 2:
                    row.set('r', str(int(row.get('r')) + 5))
                    for cell in row:
                        ref = cell.get('r')
                        col = ''.join(c for c in ref if c.isalpha())
                        cell.set('r', col + str(int(''.join(c for c in ref if c.isdigit())) + 5))
            target = tree.find('.//s:c[@r="D7"]', NS)
            target.find('s:v', NS).text = '2.5'
        self.assert_issue(self.changed('xl/worksheets/sheet3.xml', edit), 'Courses', 'D7', 'integer')

    def test_corrupt_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'outputs') as folder:
            path = Path(folder) / 'bad.xlsx'
            path.write_bytes(b'not a zip')
            self.assert_issue(path, 'Workbook', '-', 'cannot read XLSX')


if __name__ == '__main__':
    unittest.main()
