import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from stillwatch import history,mesh,phone

class PhoneBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        with patch.object(history,'DATABASE',Path(self.temp.name)/'history.sqlite3'):self.db=history.connect()
        mesh.set_authority(self.db,mesh.identity(self.db)[0]);self.db.commit()
        self.device=str(uuid.uuid4());self.label='Example Phone'
    def tearDown(self):self.db.close();self.temp.cleanup()
    def event(self,cloud=False):
        return {'operation':str(uuid.uuid4()),'kind':'event','payload':{
            'event':str(uuid.uuid4()),'device':self.device,'label':self.label,'received':time.time(),
            'app':'Example Chat','title':'Direct ping','body':'Can you review the example plan?','cloud_allowed':cloud,
        }}
    def exchange(self,items,after=0):
        return phone.exchange(self.db,self.device,self.label,{'version':1,'device':self.device,'after':after,'operations':items})
    def test_default_phone_event_stays_local_and_exclusion_is_shared(self):
        response=self.exchange([self.event()])
        self.assertEqual(response['states'][0]['decision'],'excluded')
        self.assertEqual(self.db.execute('SELECT decision FROM notifications').fetchone()[0],'excluded')
        self.assertEqual(self.db.execute("SELECT count(*) FROM mesh_operations WHERE kind='decision'").fetchone()[0],1)
    def test_opted_in_app_is_available_to_authoritative_classifier(self):
        response=self.exchange([self.event(True)])
        self.assertEqual(response['states'][0]['decision'],'unclassified')
    def test_phone_cannot_submit_decisions_or_impersonate_a_device(self):
        item=self.event();item['payload']['device']=str(uuid.uuid4())
        with self.assertRaises(ValueError):self.exchange([item])
        item=self.event();item['kind']='decision'
        with self.assertRaises((ValueError,KeyError)):self.exchange([item])
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],0)
    def test_retry_is_idempotent_and_cursor_is_monotonic(self):
        item=self.event()
        first=self.exchange([item]);second=self.exchange([item],first['cursor'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],1)
        self.assertEqual(second['cursor'],first['cursor'])
        self.assertEqual(second['states'],[])
    def test_phone_dismissal_updates_shared_desktop_state(self):
        item=self.event(True);first=self.exchange([item])
        op={'operation':str(uuid.uuid4()),'kind':'dismiss','payload':{'events':[item['payload']['event']]}}
        second=self.exchange([op],first['cursor'])
        self.assertTrue(second['states'][0]['handled'])
    def test_desktop_dismissal_reaches_phone(self):
        item=self.event(True);first=self.exchange([item])
        ident=self.db.execute('SELECT id FROM notifications').fetchone()[0]
        mesh.dismiss(self.db,ident)
        self.assertTrue(self.exchange([],first['cursor'])['states'][0]['handled'])
    def test_phone_response_has_no_api_keys_or_private_service_settings(self):
        response=self.exchange([self.event()])
        self.assertEqual(set(response),{'version','authority','cursor','ack','states','removed_events'})
        self.assertNotIn('token',json.dumps(response))
        self.assertNotIn('key_file',json.dumps(response))
    def test_large_unicode_backlog_advances_in_bounded_mobile_pages(self):
        items=[self.event(True) for _ in range(24)]
        for index,item in enumerate(items):
            item['payload']['title']=str(index)+'界'*9990
            item['payload']['body']='界'*10000
        response=self.exchange(items)
        seen=set()
        while response['states']:
            self.assertLess(len(json.dumps(response).encode()),phone.MAX_BYTES)
            seen.update(state['id'] for state in response['states'])
            response=self.exchange([],response['cursor'])
        self.assertEqual(len(seen),24)
    def test_token_authentication_uses_hashes_and_rejects_wrong_credentials(self):
        import hashlib
        token='fixture-phone-token-only'
        path=Path(self.temp.name)/'clients.json'
        path.write_text(json.dumps({self.device:{'token_hash':hashlib.sha256(token.encode()).hexdigest(),'label':self.label}}))
        with patch.object(phone,'CLIENTS',path):
            self.assertEqual(phone.authenticate(token),(self.device,self.label))
            self.assertIsNone(phone.authenticate('different-fixture-token'))
    def test_bridge_requires_authoritative_server(self):
        mesh.set_authority(self.db,str(uuid.uuid4()));self.db.commit()
        with self.assertRaises(RuntimeError):self.exchange([])
    def test_rejected_batch_is_atomic(self):
        one=self.event();two=self.event();two['payload']['cloud_allowed']='yes'
        with self.assertRaises(ValueError):self.exchange([one,two])
        self.assertEqual(self.db.execute('SELECT count(*) FROM notifications').fetchone()[0],0)


class PhoneTlsTests(unittest.TestCase):
    def test_private_tls_api_requires_token_and_ingests_a_fixture(self):
        import hashlib,http.client,ssl,subprocess,threading
        from http.server import ThreadingHTTPServer
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cert=root/'cert.pem';key=root/'key.pem'
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-keyout',str(key),'-out',str(cert),'-days','1','-subj','/CN=localhost'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            device=str(uuid.uuid4());token='fixture-tls-token-only'
            clients=root/'clients.json';clients.write_text(json.dumps({device:{'token_hash':hashlib.sha256(token.encode()).hexdigest(),'label':'Example Phone'}}))
            with patch.object(history,'DATABASE',root/'history.sqlite3'),patch.object(phone,'CLIENTS',clients):
                db=history.connect();mesh.set_authority(db,mesh.identity(db)[0]);db.commit();db.close()
                server=ThreadingHTTPServer(('127.0.0.1',0),phone.Handler)
                context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(cert,key)
                server.socket=context.wrap_socket(server.socket,server_side=True)
                thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
                client_context=ssl.create_default_context(cafile=str(cert))
                port=server.server_address[1]
                try:
                    connection=http.client.HTTPSConnection('localhost',port,context=client_context)
                    connection.request('POST','/v1/phone/exchange',body='{}',headers={'Content-Type':'application/json'})
                    self.assertEqual(connection.getresponse().status,401);connection.close()
                    payload={'version':1,'device':device,'after':0,'operations':[]}
                    connection=http.client.HTTPSConnection('localhost',port,context=client_context)
                    connection.request('POST','/v1/phone/exchange',body=json.dumps(payload),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
                    response=connection.getresponse();self.assertEqual(response.status,200)
                    self.assertEqual(json.loads(response.read())['states'],[]);connection.close()
                finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
