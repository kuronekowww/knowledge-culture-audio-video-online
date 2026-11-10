import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import artifacts
import batch_runner
import assemble_draft
import production_checks
import flow
import generate_images
import run
from PIL import Image


class TeamBatchTests(unittest.TestCase):
    def test_hash_session_reads_bytes_once_and_observes_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'frame.png'
            path.write_bytes(b'1234')
            with patch.object(hashlib, 'sha256', wraps=hashlib.sha256) as hash_fn:
                with artifacts.hash_session():
                    first = artifacts.file_hash(path)
                    self.assertEqual(first, artifacts.file_hash(path))
                    self.assertEqual(hash_fn.call_count, 1)
                    replacement = path.with_suffix('.tmp')
                    replacement.write_bytes(b'4321')
                    original_time = path.stat().st_mtime_ns
                    os.utime(replacement, ns=(original_time, original_time))
                    replacement.replace(path)
                    changed = artifacts.file_hash(path)
                    self.assertNotEqual(first, changed)
                self.assertEqual(changed, artifacts.file_hash(path))
                self.assertEqual(hash_fn.call_count, 3)

    def test_scoped_batch_and_read_only_status(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            jobs = [base/f'101-K{i:02}' for i in range(10)]
            for job in jobs:
                artifacts.atomic_json(job/'config.json', {'album_id':'101','highlight_id':job.name.split('-')[1]})
            manifest = base/'batch.json'
            artifacts.atomic_json(manifest, {'jobs':[j.name for j in jobs]})
            self.assertEqual(flow.select_jobs(jobs, ['K02', jobs[8].name]), [jobs[2],jobs[8]])
            with self.assertRaises(ValueError):
                flow.select_jobs(jobs, ['K99'])
            called = []
            def work(job):
                called.append(job)
                return 0
            allowed = [jobs[2],jobs[8]]
            with patch.object(batch_runner.production_io, 'preflight', return_value={'ok':True}) as check, \
                    patch.object(run, 'run_pipeline', side_effect=work):
                result = batch_runner.run_batch(manifest, selected_jobs=allowed)
                check.assert_called_once_with(allowed)
            self.assertCountEqual(called, allowed)
            self.assertEqual((result['selected'],result['manifest_total']), (2,10))
            log = base/'batch.flow.log'
            artifacts.atomic_json(jobs[2]/'flow.last.json', {'job':str(jobs[2]), 'status':'awaiting_agent','observed_at':1})
            before = sorted((p, p.stat().st_mtime_ns) for p in base.rglob('*'))
            with patch('flow.execute', side_effect=AssertionError('read-only')), \
                    patch('flow.summary', side_effect=AssertionError('no revalidation')):
                result = flow.last_status(flow.jobs_in(manifest), log)
            self.assertEqual(result['jobs'][2]['status'],'awaiting_agent')
            self.assertEqual(result['jobs'][0]['status'],'no_flow_summary')
            self.assertEqual(before, sorted((p, p.stat().st_mtime_ns) for p in base.rglob('*')))

    def test_bad_input_blocks_only_its_job(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            jobs = [root/'one', root/'two']
            for job in jobs:
                artifacts.atomic_json(job/'config.json', {'album_id':'101'})
            manifest = root/'batch.json'
            artifacts.atomic_json(manifest, {'jobs':[j.name for j in jobs]})
            failed = {'name':'catalog/one','job':str(jobs[0]),'ok':False,'error':'missing source'}
            with patch.object(batch_runner.production_io, 'preflight', return_value={'ok':False,'checks':[failed]}), \
                    patch.object(run, 'run_pipeline', return_value=0) as proceed:
                report = batch_runner.run_batch(manifest)
            proceed.assert_called_once_with(jobs[1])
            self.assertEqual(report['jobs'][str(jobs[0])]['phase'], 'preflight')
            self.assertIn('missing source', report['jobs'][str(jobs[0])]['error'])

    def test_unapproved_members_never_enter_preflight(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            jobs = [root/'approved', root/'waiting']
            for job in jobs:
                artifacts.atomic_json(job/'config.json', {'album_id':'123'})
            title = {'job':str(jobs[1]),'stage':'title','status':'awaiting_user_title'}
            with patch('title_review.pending', side_effect=lambda job, cfg: title if job == jobs[1] else None), \
                    patch.object(batch_runner, 'run_batch', return_value={'jobs':{}}) as dispatch, \
                    patch.object(flow, 'summary', return_value={'job':str(jobs[0]),'status':'awaiting_agent'}) as summarise:
                report = flow.execute(jobs, root/'batch.flow.log', root/'batch.json')
            self.assertEqual(dispatch.call_args.kwargs['selected_jobs'], jobs[:1])
            summarise.assert_called_once_with(jobs[0], None)
            self.assertEqual(report['jobs'][1], title)
            with patch('title_review.pending', return_value=title), \
                    patch.object(batch_runner, 'run_batch', side_effect=AssertionError('no preflight')):
                self.assertEqual(flow.execute(jobs[1:], root/'batch.flow.log', root/'batch.json')['jobs'], [title])

    def test_completed_image_is_not_rewritten_and_bad_cache_is_repaired(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            row = {'id':'SH1','prompt':'a single shot','filename':'shot.png','size':'1536x1024'}
            api = 'https://example.invalid'
            fp = generate_images.image_fingerprint(api, row, 'low')
            target = root/'shot.png'
            cache = root/'cache'
            media = cache/fp/'image.png'
            media.parent.mkdir(parents=True)
            Image.new('RGB', (100,60), 'blue').save(target)
            media.write_bytes(target.read_bytes())
            checksum = artifacts.file_hash(target)
            receipt = {'fingerprint':fp,'status':'complete','sha256':checksum}
            local = root/'.receipts'/'SH1.json'
            artifacts.atomic_json(local, receipt)
            artifacts.atomic_json(media.parent/'request.json', receipt)
            args = (api, '', row, root, 'low', 1, 1, cache)
            original = (target.stat().st_mtime_ns,local.stat().st_mtime_ns)
            with patch.object(generate_images, 'request_image', side_effect=AssertionError('no paid submission')):
                with patch.object(generate_images, 'atomic_copy', side_effect=AssertionError('no duplicate copy')):
                    self.assertEqual(generate_images._prepare_one(*args)['status'], 'skipped')
                media.write_bytes(b'bad cache')
                self.assertEqual(generate_images._prepare_one(*args)['status'], 'skipped')
            self.assertEqual(artifacts.file_hash(media), checksum)
            self.assertEqual((target.stat().st_mtime_ns,local.stat().st_mtime_ns), original)

    def test_assemble_ready_preserves_attempts_and_continues_after_error(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            jobs = [root/name for name in ('already','pending','error','ready','openfilm','mp4')]
            for job in jobs:
                cfg = {'album_id':'1'}
                if job.name == 'openfilm':
                    cfg['output_backend'] = 'openfilm'
                if job.name == 'mp4':
                    cfg['delivery'] = {'target':'mp4','choice_evidence':'requested'}
                artifacts.atomic_json(job/'config.json', cfg)
            statuses = {'already':'draft_verified','pending':'awaiting_user_title',
                        'error':'ready_for_local_assembly','ready':'ready_for_local_assembly'}
            called = []
            def assemble(job, cfg):
                called.append(job.name)
                if job.name == 'error':
                    raise ValueError('写入结果未知，保留回执')
                return {'status':'draft_verified','draft_dir':str(job/'draft')}
            with patch.object(flow, 'summary', side_effect=lambda job: {'job':str(job),'status':statuses[job.name]}), \
                    patch.object(assemble_draft, 'assemble', side_effect=assemble):
                report = flow.assemble_ready(jobs, root/'batch.json')
            self.assertEqual(called, ['error','ready'])
            self.assertEqual(report['drafts_verified'],2)
            self.assertEqual(report['assembled_now'],1)
            self.assertEqual(report['status'],'failed')
            self.assertEqual(report['jobs'][2]['message'],'写入结果未知，保留回执')
            self.assertFalse((jobs[-1]/'flow.last.json').exists())

    def test_full_track_reuses_grounded_entry_only_for_same_source(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            audio = job/'source'
            audio.write_bytes(b'original')
            content = {k:'内容依据' for k in ('problem','demand_reason','delivered_value','conclusion',
                            'complete_reason','opening_hook','return_hook_omission_reason')}
            content.update(is_complete=True,is_intro_only=False,evidence=[{'start':1,'end':2,'text':'原文'}])
            plan = {'selection_mode':'full_track','source_audio':str(audio),'track_id':'7',
                    'clips':[{'start':0,'end':310}],'content':content}
            artifacts.atomic_json(job/'cut_plan.json',plan)
            track = {'ok':True,'track_id':'7','selection_mode':'full_track','actual_seconds':310,
                     'source_sha256':artifacts.file_hash(audio),
                     'content_review':{k:content[k] for k in ('is_complete','is_intro_only','complete_reason')}}
            artifacts.atomic_json(job/'entry_preflight.json',{'ok':True,'tracks':[track]})
            with patch.object(production_checks,'cut_request',side_effect=ValueError('缺少真实切点评估')) as detailed:
                self.assertTrue(production_checks.cut_ready(job, {}))
                detailed.assert_not_called()
                audio.write_bytes(b'changed')
                self.assertFalse(production_checks.cut_ready(job, {}))
                detailed.assert_called_once()
                audio.write_bytes(b'original')
                plan['selection_mode'] = 'excerpt'
                artifacts.atomic_json(job/'cut_plan.json',plan)
                self.assertFalse(production_checks.cut_ready(job, {}))
                self.assertEqual(detailed.call_count,2)


if __name__ == '__main__':
    unittest.main()
