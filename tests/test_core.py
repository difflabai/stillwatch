import importlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from stillwatch import decision
from stillwatch.settings import Settings

def answer(probability=.95, category='agent', severity='attention', severe_probability=.02):
    return {'answers': {
        'attention': {'type': 'choice', 'choice': 'interrupt' if probability > .5 else 'history', 'probabilities': {'interrupt': probability, 'history': 1-probability}},
        'category': {'type': 'choice', 'choice': category},
        'severity': {'type': 'choice', 'choice': severity, 'probabilities': {'severe': severe_probability, 'attention': 1-severe_probability}},
    }}

class DecisionTests(unittest.TestCase):
    def test_sudo_stays_amber(self):
        self.assertEqual(decision.parse_answer(answer())['severity'], 'attention')

    def test_agent_approval_at_point_seven_one_gets_amber_attention(self):
        result = decision.parse_answer(answer(probability=.71))
        self.assertTrue(decision.wants_attention(result, {'threshold': .85}))
        self.assertEqual(result['severity'], 'attention')

    def test_routine_agent_completion_does_not_interrupt(self):
        result = decision.parse_answer(answer(probability=.4))
        self.assertFalse(decision.wants_attention(result, {'threshold': .85}))
        # A contradictory output must not bypass the model's history choice.
        result.update(probability=.99, attention_choice='history')
        self.assertFalse(decision.wants_attention(result, {'threshold': .85}))

    def test_other_categories_keep_the_general_threshold(self):
        result = decision.parse_answer(answer(probability=.71, category='other'))
        self.assertFalse(decision.wants_attention(result, {'threshold': .85}))

    def test_agent_threshold_can_be_overridden(self):
        result = decision.parse_answer(answer(probability=.71))
        self.assertFalse(decision.wants_attention(result, {'threshold': .85, 'category_thresholds': {'agent': .9}}))

    def test_distinct_agent_requests_with_same_title_have_distinct_cooldowns(self):
        one = decision.issue_key('Codex','Awaiting approval','agent','Approve installation of a dependency')
        two = decision.issue_key('Codex','Awaiting approval','agent','Approve changing the deployment configuration')
        self.assertNotEqual(one,two)

    def test_red_requires_severe_probability(self):
        self.assertEqual(decision.parse_answer(answer(severity='severe', severe_probability=.84))['severity'], 'attention')
        self.assertEqual(decision.parse_answer(answer(severity='severe', severe_probability=.95))['severity'], 'severe')

    def test_invalid_model_values_are_rejected(self):
        for probability in [True, -1, 2, float('nan'), float('inf'), '0.95']:
            with self.subTest(probability=probability), self.assertRaises(ValueError):
                decision.parse_answer(answer(probability=probability) if isinstance(probability, (int,float)) else {'answers': {'attention': {'type': 'choice', 'choice': 'interrupt', 'probabilities': {'interrupt': probability}}}})

    def test_unknown_categories_are_rejected(self):
        with self.assertRaises(ValueError):
            decision.parse_answer(answer(category='execute-command'))

    def test_native_payload_and_authorization_are_separate(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def read(self): return json.dumps(answer()).encode()
        requests = []
        def send(request, **kwargs):
            requests.append(request)
            return Response()
        with patch.dict(os.environ, {'NANOGPT_API_KEY': 'fixture-only-key'}), patch.object(decision.urllib.request, 'urlopen', side_effect=send):
            result = decision.classify('Agent', 'Needs approval', 'Wait for input', {'timeout_seconds': 5})
        payload = json.loads(requests[0].data)
        self.assertNotIn('fixture-only-key', requests[0].data.decode())
        self.assertEqual(set(payload['questions']), {'attention','category','severity'})
        self.assertEqual(result['category'], 'agent')

    def test_policy_rejects_invalid_intervals(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'config.json'
            file.write_text(json.dumps({'enabled': True, 'cooldown_seconds': -1}))
            with patch.object(decision, 'POLICY', file), self.assertRaises(ValueError):
                decision.config()

    def test_policy_rejects_invalid_category_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / 'config.json'
            for values in ({'agent':True},{'agent':-1},{'agent':float('nan')},{'unknown':.6},[]):
                file.write_text(json.dumps({'enabled':True,'category_thresholds':values}))
                with patch.object(decision,'POLICY',file),self.assertRaises(ValueError):
                    decision.config()

    def test_excluded_app_makes_no_cloud_request(self):
        with patch.object(decision.urllib.request, 'urlopen') as request:
            with self.assertRaises(PermissionError):
                decision.classify('Sensitive App', 'Private message', 'Fixture text', {'excluded_apps': ['sensitive*']})
            request.assert_not_called()

class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environ = patch.dict(os.environ, {'XDG_STATE_HOME': self.temp.name})
        self.environ.start()
        import stillwatch.paths
        import stillwatch.history
        importlib.reload(stillwatch.paths)
        self.history = importlib.reload(stillwatch.history)
        self.db = self.history.connect()

    def tearDown(self):
        self.db.close()
        self.environ.stop()
        self.temp.cleanup()

    def add(self, title='Event', category='other', severity='attention', decision_value='quiet'):
        ident = self.history.record(self.db, 'Example App', title, '<b>Safe text</b>')
        self.db.execute('UPDATE notifications SET category=?,severity=?,decision=? WHERE id=?', (category,severity,decision_value,ident))
        self.db.commit()
        return ident

    def test_severe_then_important_then_routine(self):
        severe = self.add('Outage','downtime','severe','immediate')
        important = self.add('Sudo','agent','attention','immediate')
        quiet = self.add('Copy complete')
        self.assertEqual([row[0] for row in self.history.search(self.db, details=True)], [severe,important,quiet])

    def test_search_treats_wildcards_literally(self):
        self.add('Progress 50% _done')
        self.add('Another event')
        self.assertEqual(len(self.history.search(self.db,'%')),1)
        self.assertEqual(len(self.history.search(self.db,'_')),1)

    def test_history_is_plain_text(self):
        self.add()
        self.assertEqual(self.history.search(self.db)[0][3], 'Safe text')
        self.assertEqual(self.history.plain('<compiler> &amp; text'), '<compiler> & text')

    def test_old_urgent_survives_until_handled(self):
        important = self.add('Pending','agent','attention','immediate')
        old = self.add('Old ordinary')
        self.db.execute('UPDATE notifications SET received=?', (time.time()-8*86400,))
        self.db.commit()
        self.history.prune(self.db)
        self.assertEqual([r[0] for r in self.history.search(self.db,details=True)], [important])
        self.db.execute('UPDATE notifications SET attention_dismissed=1 WHERE id=?',(important,))
        self.db.commit()
        self.history.prune(self.db)
        self.assertEqual(self.history.search(self.db),[])

    def test_ordinary_entry_cap(self):
        self.db.executemany('INSERT INTO notifications(received,app,title,body) VALUES (?,?,?,?)', ((time.time(),'Example',str(i),'') for i in range(1005)))
        self.history.prune(self.db)
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],1000)

    def test_storage_is_private(self):
        self.assertEqual(self.history.DATABASE.stat().st_mode & 0o777,0o600)
        self.assertEqual(self.history.STATE.stat().st_mode & 0o777,0o700)

    def test_cooldown_is_immediate_then_five_minutes(self):
        key=decision.issue_key('Agent','Sudo retry 1','agent')
        self.assertEqual(key,decision.issue_key('Agent','Sudo retry 2','agent'))
        self.assertTrue(decision.allow_alert(self.db,key,'attention',now=1000))
        self.db.commit()
        self.db.close()
        self.db=self.history.connect()
        self.assertFalse(decision.allow_alert(self.db,key,'attention',now=1299))
        self.assertTrue(decision.allow_alert(self.db,key,'attention',now=1300))

    def test_other_issue_and_escalation_bypass_cooldown(self):
        self.assertTrue(decision.allow_alert(self.db,'one','attention',now=1000))
        self.assertTrue(decision.allow_alert(self.db,'two','attention',now=1001))
        self.assertTrue(decision.allow_alert(self.db,'one','severe',now=1002))
        self.assertFalse(decision.allow_alert(self.db,'one','severe',now=1003))

    def test_open_modal_does_not_get_another_repeat(self):
        ident=self.add(decision_value='immediate',severity='severe')
        self.db.execute('UPDATE notifications SET issue_key=? WHERE id=?',('active',ident));self.db.commit()
        self.assertFalse(decision.allow_alert(self.db,'active','attention',now=5000))
        self.assertFalse(decision.allow_alert(self.db,'active','severe',now=5000))

class RestoreTests(unittest.TestCase):
    def test_later_user_edits_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'settings.json'
            file.write_text(json.dumps([{'schema':'example','key':'theme','before':"'old'",'after':"'blue'",'scope':'profile'}]))
            settings=Settings(file)
            with patch.object(settings,'get',return_value="'custom'"),patch('stillwatch.settings.subprocess.run') as run:
                settings.restore('profile')
                run.assert_not_called()

    def test_unchanged_setting_restores_only_its_original_value(self):
        with tempfile.TemporaryDirectory() as directory:
            file=Path(directory)/'settings.json'
            file.write_text(json.dumps([{'schema':'example','key':'theme','before':"'old'",'after':"'blue'",'scope':'profile'}]))
            settings=Settings(file)
            with patch.object(settings,'get',return_value="'blue'"),patch('stillwatch.settings.subprocess.run') as run:
                settings.restore('profile')
                run.assert_called_once_with(['gsettings','set','example','theme',"'old'"],check=True)

    def test_kitty_restore_preserves_unrelated_later_edits(self):
        from stillwatch import profile
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            state=root/'state';state.mkdir()
            kitty=root/'.config/kitty/kitty.conf';kitty.parent.mkdir(parents=True)
            (state/'kitty-original.conf').write_text('font_size 12.0\nmodify_font cell_height 108%\nbackground #111111\n')
            kitty.write_text('font_size 10.5\nmodify_font cell_height 100%\nbackground #222222\n')
            with patch.object(profile,'STATE_DIR',state),patch.object(profile.Path,'home',return_value=root),patch.object(profile,'Settings'):
                profile.restore()
            self.assertIn('font_size 12.0',kitty.read_text())
            self.assertIn('background #222222',kitty.read_text())

class InstallTests(unittest.TestCase):
    def test_dry_run_does_not_mutate_or_read_a_key(self):
        from stillwatch import install
        with patch.object(install.os,'getuid',return_value=1000),patch.object(install,'session_environment'),patch.object(install,'locations',return_value={'application':Path('/example/stillwatch')}),patch.object(install,'private_write') as write,patch.object(install,'save_key') as key,patch.object(install.subprocess,'run') as run:
            install.install(dry_run=True,enable_ai=True)
            write.assert_not_called();key.assert_not_called();run.assert_not_called()

if __name__ == '__main__':
    unittest.main()
