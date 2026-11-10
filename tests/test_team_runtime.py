import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import runtime_doctor


class TeamRuntime(unittest.TestCase):
    def test_cache_and_refresh_use_the_selected_dependencies(self):
        with tempfile.TemporaryDirectory() as temp:
            record=Path(temp)/'runtime-environment.json'
            first=runtime_doctor.inspect(record,sys.executable)
            self.assertTrue(first['ok'],first['errors'])
            self.assertEqual(set(first['python']['dependencies']),{'requests','PIL'})
            with patch.object(runtime_doctor,'python_probe',side_effect=AssertionError('unexpected process')):
                self.assertTrue(runtime_doctor.inspect(record,sys.executable)['cache_hit'])
            data=json.loads(record.read_text())
            path=next(iter(data['watched_files']))
            data['watched_files'][path][1]-=1
            record.write_text(json.dumps(data))
            self.assertFalse(runtime_doctor.inspect(record,sys.executable)['cache_hit'])
            self.assertFalse(runtime_doctor.inspect(record,sys.executable,refresh=True)['cache_hit'])
            self.assertIn('numpy',runtime_doctor.inspect(record,sys.executable,mp4=True)['python']['dependencies'])

    def test_report_copied_from_another_host_is_rechecked(self):
        with tempfile.TemporaryDirectory() as temp:
            record=Path(temp)/'runtime-environment.json'
            first=runtime_doctor.inspect(record,sys.executable)
            self.assertTrue(first['ok'])
            with patch.object(runtime_doctor.platform,'node',return_value='different'):
                second=runtime_doctor.inspect(record,sys.executable)
            self.assertFalse(second['cache_hit'])

    def test_missing_explicit_python_is_not_silently_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            missing=Path(temp)/'no-python'
            result=runtime_doctor.inspect(Path(temp)/'runtime.json',str(missing))
            self.assertFalse(result['ok'])
            self.assertEqual([row['executable'] for row in result['attempts']],[str(missing)])


if __name__=='__main__':
    unittest.main()
