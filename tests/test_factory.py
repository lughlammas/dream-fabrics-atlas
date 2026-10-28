import json
import io
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from pathlib import Path

from factory_store import FactoryStore, StoreError
from factory_tts import Production, TTSError, segments
from factory_server import create_handler


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FactoryStore(self.tmp.name)
        self.project = self.store.create('A Voz do Livro', 'Autora', 'pt-BR')
        self.pid = self.project['id']

    def test_create_edit_import_and_original(self):
        self.assertEqual(self.store.get(self.pid)['manifest']['title'], 'A Voz do Livro')
        project = self.store.import_text(self.pid, 'manuscrito.md', '# Abertura\n\nOlá.\n\n## Segundo\n\nMundo.')
        self.assertEqual(len(project['chapters']), 2)
        name = project['chapters'][0]['name']
        self.store.save_chapter(self.pid, name, '# Abertura\n\nTexto alterado.')
        originals = list((Path(self.tmp.name)/self.pid/'originals').glob('*.md'))
        self.assertEqual(len(originals), 1)
        self.assertIn('Olá.', originals[0].read_text(encoding='utf-8'))
        self.assertIn('Texto alterado.', self.store.get(self.pid)['chapters'][0]['text'])
        names = [c['name'] for c in self.store.get(self.pid)['chapters']]
        self.store.reorder(self.pid, list(reversed(names)))
        self.assertEqual(self.store.get(self.pid)['manifest']['chapters'], list(reversed(names)))

    def test_reject_bad_chapter_and_missing_consent(self):
        with self.assertRaises(StoreError):
            self.store.save_chapter(self.pid, '../manifest.json', 'bad')
        chapter = self.store.add_chapter(self.pid, 'Teste', 'Texto.')
        with self.assertRaises(TTSError):
            Production(self.store).start(self.pid, [chapter['chapters'][0]['name']], {'consent': False})

    def test_segment_limit_and_recovery(self):
        chunks = segments('Uma frase curta. '*600, 100)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))
        self.assertIn('Uma frase curta.', ' '.join(chunks))

    def test_render_wav_with_fake_provider_and_reuse_segments(self):
        project = self.store.add_chapter(self.pid, 'Abertura', '# Abertura\n\nUma frase de teste.')
        name = project['chapters'][0]['name']

        class FakeProvider:
            calls = 0
            def synthesize(self, text, voice, fmt, speed, instructions):
                self.calls += 1
                output = io.BytesIO()
                with wave.open(output, 'wb') as wav:
                    wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(8000)
                    wav.writeframes(b'\0\0' * 800)
                return output.getvalue()

        provider = FakeProvider()
        production = Production(self.store)
        for _ in range(2):
            job = {'state':'running','completed':0,'total':1,'current':'','error':'','failed':[]}
            production._run(self.pid, [name], provider, 'wav', 'coral', 1, '', job)
            self.assertEqual(job['state'], 'done')
        self.assertEqual(provider.calls, 1)
        self.assertEqual(len(self.store.get(self.pid)['chapters'][0]['audio']), 1)
        audio = self.store.file(self.pid, 'audio', Path(name).stem+'.wav')
        with wave.open(str(audio), 'rb') as wav:
            self.assertEqual(wav.getnframes(), 800)
        self.store.save_chapter(self.pid, name, '# Abertura\n\nTexto novo.')
        self.assertEqual(self.store.get(self.pid)['chapters'][0]['audio'], [])


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler(FactoryStore(self.tmp.name)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = 'http://127.0.0.1:%s' % self.server.server_port

    def request(self, path, method='GET', body=None, origin=None):
        headers = {'Content-Type':'application/json'}
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.base+path, data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method=method)
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read())

    def test_local_api_flow(self):
        created = self.request('/api/projects', 'POST', {'title':'Livro', 'author':'Pessoa','language':'pt-BR'})
        pid = created['id']
        chapter = self.request('/api/projects/'+pid+'/chapters','POST',{'title':'Início','text':'# Início\n\nOlá.'})
        self.assertEqual(len(chapter['chapters']), 1)
        fetched = self.request('/api/projects/'+pid)
        self.assertEqual(fetched['chapters'][0]['text'], '# Início\n\nOlá.')
        self.assertEqual(len(self.request('/api/projects')), 1)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/projects/'+pid+'/production','POST',{'names':[chapter['chapters'][0]['name']]})
        self.assertEqual(ctx.exception.code, 400)

    def test_cross_origin_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/projects','POST',{'title':'A'},'https://other.example')
        self.assertEqual(ctx.exception.code, 400)

    def test_music_draft_routes_do_not_start_generation(self):
        created = self.request('/api/projects', 'POST', {'title':'Livro', 'author':'Pessoa','language':'pt-BR'})
        pid = created['id']
        track = self.request('/api/projects/'+pid+'/music', 'POST',
                             {'title':'Tema','prompt':'Piano sereno','duration':30,'seed':7,'steps':8})
        self.assertEqual(track['status'], 'draft')
        listed = self.request('/api/projects/'+pid+'/music')
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]['title'], 'Tema')
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.request('/api/projects/'+pid+'/music/'+track['id']+'/generate', 'POST', {'consent':False})
        self.assertEqual(ctx.exception.code, 400)

    def test_music_reference_upload_and_playback_route(self):
        pid = self.request('/api/projects', 'POST', {'title':'Livro','author':'Pessoa'})['id']
        track = self.request('/api/projects/'+pid+'/music','POST',
                             {'title':'Tema','prompt':'Piano','duration':30})
        output = io.BytesIO()
        with wave.open(output, 'wb') as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b'\0\0' * 100)
        audio = output.getvalue()
        route = '/api/projects/'+pid+'/music/'+track['id']+'/reference'
        req = urllib.request.Request(self.base+route, audio, {'Content-Type':'audio/wav',
                                      'X-File-Name':'teste.wav'}, method='PUT')
        with urllib.request.urlopen(req) as response:
            saved = json.loads(response.read())
        self.assertEqual(saved['reference']['label'], 'teste.wav')
        with urllib.request.urlopen(self.base+saved['reference']['url']) as response:
            self.assertEqual(response.read(), audio)
        with self.assertRaises(urllib.error.HTTPError):
            urllib.request.urlopen(urllib.request.Request(self.base+route, b'fake',
                                   {'Content-Type':'audio/wav'}, method='PUT'))


if __name__ == '__main__':
    unittest.main()
