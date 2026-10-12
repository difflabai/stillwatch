import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from stillwatch import mesh, sync
from stillwatch import history
from stillwatch.decision import issue_key

class SharedStateTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.databases=[]
        for name in ('laptop','workstation'):
            with patch.object(history,'DATABASE',Path(self.directory.name)/(name+'.sqlite3')):
                db=history.connect()
            mesh.identity(db,name.title())
            db.commit()
            self.databases.append(db)
        self.laptop,self.workstation=self.databases

    def tearDown(self):
        for db in self.databases:db.close()
        self.directory.cleanup()

    def add(self,db,title='Direct ping',body='Please review the plan',app='Slack',stamp=1000):
        ident,created=mesh.capture(db,app,title,body,received=stamp)
        db.commit()
        return ident

    def transfer(self):
        def transport(payload):
            return json.dumps(mesh.exchange(self.workstation,json.loads(payload))).encode()
        sync.once(self.laptop,'fixture-peer',invoke=transport)

    def settle(self):
        for _ in range(4):self.transfer()

    def ids(self,db):
        return [row[0] for row in db.execute('SELECT id FROM notifications')]

    def handled(self,db):
        return db.execute('SELECT attention_dismissed FROM notifications').fetchone()[0]

    def test_matching_deliveries_share_one_notification_on_each_device(self):
        self.add(self.laptop)
        self.add(self.workstation,app='com.slack.Slack',stamp=1002)
        self.settle()
        for db in self.databases:
            self.assertEqual(len(self.ids(db)),1)
            self.assertEqual(mesh.sources(db,self.ids(db)[0]),['Laptop','Workstation'])

    def test_unique_workstation_event_reaches_laptop(self):
        self.add(self.workstation,title='Meeting in ten minutes',body='Project sync')
        self.settle()
        self.assertEqual(self.laptop.execute('SELECT title FROM notifications').fetchone()[0],'Meeting in ten minutes')

    def test_different_messages_numbers_and_occurrences_remain_distinct(self):
        self.add(self.laptop,body='Deploy build 101')
        self.add(self.workstation,body='Deploy build 102')
        self.add(self.workstation,body='Deploy build 101',stamp=1301)
        self.settle()
        self.assertEqual(len(self.ids(self.laptop)),3)

    def test_one_classification_is_shared_for_duplicate_deliveries(self):
        ident=self.add(self.laptop)
        self.add(self.workstation)
        key=issue_key('Slack','Direct ping','slack')
        mesh.publish_decision(self.laptop,ident,'immediate',.98,'slack','attention',key)
        self.settle()
        for db in self.databases:
            self.assertEqual(db.execute('SELECT decision,probability,category FROM notifications').fetchone(),('immediate',.98,'slack'))
            self.assertEqual(db.execute("SELECT count(*) FROM mesh_operations WHERE kind='decision'").fetchone()[0],1)

    def test_dismissal_from_either_device_handles_all_deliveries(self):
        for side in ('laptop','workstation'):
            with self.subTest(side=side):
                title='Ping on '+side
                self.add(self.laptop,title=title)
                self.add(self.workstation,title=title)
                self.settle()
                chosen=getattr(self,side)
                ident=chosen.execute('SELECT id FROM notifications WHERE title=?',(title,)).fetchone()[0]
                mesh.dismiss(chosen,ident)
                self.settle()
                for db in self.databases:
                    self.assertEqual(db.execute('SELECT attention_dismissed FROM notifications WHERE title=?',(title,)).fetchone()[0],1)

    def test_late_duplicate_inherits_dismissal(self):
        ident=self.add(self.laptop)
        mesh.dismiss(self.laptop,ident)
        self.add(self.workstation,stamp=1002)
        self.settle()
        self.assertTrue(self.handled(self.laptop))
        self.assertTrue(self.handled(self.workstation))

    def test_offline_dismissal_survives_restart_and_reconnect(self):
        ident=self.add(self.laptop)
        self.settle()
        remote=self.ids(self.workstation)[0]
        mesh.dismiss(self.workstation,remote)
        self.workstation.close()
        with patch.object(history,'DATABASE',Path(self.directory.name)/'workstation.sqlite3'):
            self.workstation=history.connect()
        self.databases[1]=self.workstation
        self.settle()
        self.assertTrue(self.handled(self.laptop))
        self.assertTrue(self.handled(self.workstation))

    def test_retry_after_lost_reply_is_idempotent(self):
        self.add(self.laptop)
        def lost_reply(payload):
            mesh.exchange(self.workstation,json.loads(payload))
            raise TimeoutError('fixture transport timeout')
        with self.assertRaises(TimeoutError):sync.once(self.laptop,'fixture-peer',invoke=lost_reply)
        self.settle()
        self.assertEqual(len(self.ids(self.workstation)),1)
        counts=[db.execute('SELECT count(*) FROM mesh_operations').fetchone()[0] for db in self.databases]
        self.settle()
        self.assertEqual(counts,[db.execute('SELECT count(*) FROM mesh_operations').fetchone()[0] for db in self.databases])

    def test_dismissed_event_cannot_revive_after_history_is_pruned(self):
        ident=self.add(self.laptop)
        item=mesh.export(self.laptop,0)[1][0]
        mesh.dismiss(self.laptop,ident)
        self.laptop.execute('DELETE FROM notifications')
        mesh.prune(self.laptop)
        self.laptop.commit()
        with self.laptop:mesh.ingest(self.laptop,[item])
        self.assertTrue(self.handled(self.laptop))

    def test_invalid_batch_rolls_back_without_partial_delivery(self):
        self.add(self.laptop)
        items=mesh.export(self.laptop,0)[1]
        bad={'operation':str(uuid.uuid4()),'kind':'event','payload':{'event':'invalid'}}
        with self.assertRaises(ValueError):mesh.exchange(self.workstation,{'version':1,'after':0,'sent':1,'operations':items+[bad]})
        self.assertEqual(self.ids(self.workstation),[])

    def test_historical_local_rows_are_not_broadcast_on_pairing(self):
        self.workstation.execute('INSERT INTO notifications(received,app,title,body) VALUES (1000,?,?,?)',('Fixture','Older private history','Fixture text'))
        self.workstation.commit()
        mesh.initialize(self.workstation)
        self.workstation.commit()
        self.settle()
        self.assertEqual(self.ids(self.laptop),[])

    def test_pairing_rejects_ssh_options_and_shell_fragments(self):
        for host in ('-oProxyCommand=anything','host; command','$(command)','host\nother'):
            with self.subTest(host=host), patch('stillwatch.install.MANIFEST') as manifest:
                manifest.exists.return_value=True
                with self.assertRaises(ValueError):sync.pair(host,'Fixture')

    def test_server_is_the_only_classifier_after_pinning(self):
        server=mesh.identity(self.workstation)[0]
        mesh.set_authority(self.workstation,server)
        self.workstation.commit()
        ident=self.add(self.laptop)
        self.settle()
        self.assertEqual(mesh.authority(self.laptop),server)
        self.assertFalse(mesh.can_classify(self.laptop))
        self.assertTrue(mesh.can_classify(self.workstation))
        with self.assertRaises(RuntimeError):
            mesh.publish_decision(self.laptop,ident,'immediate',.99,'agent','attention','a'*64)
        remote=self.ids(self.workstation)[0]
        mesh.publish_decision(self.workstation,remote,'immediate',.99,'agent','attention','a'*64)
        self.settle()
        self.assertEqual(self.laptop.execute('SELECT decision FROM notifications').fetchone()[0],'immediate')

    def test_non_authority_decision_cannot_override_server(self):
        ident=self.add(self.laptop)
        self.settle()
        server=mesh.identity(self.workstation)[0]
        mesh.set_authority(self.laptop,server)
        event=self.laptop.execute('SELECT event FROM mesh_events LIMIT 1').fetchone()[0]
        item={'operation':str(uuid.uuid4()),'kind':'decision','payload':{
            'event':event,'producer':mesh.identity(self.laptop)[0],'decision':'immediate',
            'probability':.99,'category':'agent','severity':'severe','issue_key':'a'*64,
        }}
        with self.laptop:mesh.ingest(self.laptop,[item])
        self.assertEqual(self.laptop.execute('SELECT decision FROM notifications WHERE id=?',(ident,)).fetchone()[0],'unclassified')

    def test_authority_replacement_requires_explicit_pairing(self):
        mesh.set_authority(self.laptop,mesh.identity(self.laptop)[0])
        mesh.set_authority(self.workstation,mesh.identity(self.workstation)[0])
        for db in self.databases:db.commit()
        def transport(payload):return json.dumps(mesh.exchange(self.workstation,json.loads(payload))).encode()
        with self.assertRaises(RuntimeError):sync.once(self.laptop,'fixture-peer',invoke=transport)
        sync.once(self.laptop,'fixture-peer',invoke=transport,adopt_authority=True)
        self.assertEqual(mesh.authority(self.laptop),mesh.identity(self.workstation)[0])

    def test_replica_can_still_dismiss_after_server_is_pinned(self):
        self.add(self.laptop)
        mesh.set_authority(self.workstation,mesh.identity(self.workstation)[0])
        self.workstation.commit()
        self.settle()
        mesh.dismiss(self.laptop,self.ids(self.laptop)[0])
        self.settle()
        self.assertTrue(self.handled(self.workstation))

if __name__=='__main__':unittest.main()
