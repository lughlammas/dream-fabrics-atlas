import io
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from factory_music import AceStepClient, MusicError, MusicStudio, inspect_reference
from factory_store import FactoryStore


def sample_wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(8000)
        writer.writeframes(b'\0\0' * 800)
    return output.getvalue()


class FakeAce(BaseHTTPRequestHandler):
    submissions = []
    multipart = []
    def log_message(self, *args):
        pass

    def _json(self, data):
        payload = json.dumps({'code': 200, 'data': data, 'error': None}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            return self._json({'status': 'ok'})
        if self.path.startswith('/v1/audio?path='):
            audio = sample_wav()
            self.send_response(200)
            self.send_header('Content-Type', 'audio/wav')
            self.send_header('Content-Length', str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)
            return
        self.send_error(404)

    def do_POST(self):
        raw = self.rfile.read(int(self.headers['Content-Length']))
        if self.headers.get('Content-Type', '').startswith('multipart/form-data'):
            self.multipart.append((self.headers['Content-Type'], raw))
            data = None
        else:
            data = json.loads(raw)
        if self.path == '/release_task':
            if data is not None:
                self.submissions.append(data)
            return self._json({'task_id': 'test-task-1234', 'status': 'queued'})
        if self.path == '/query_result':
            return self._json([{'task_id': data['task_id_list'][0], 'status': 1,
                                'result': json.dumps([{'file': '/v1/audio?path=%2Ftmp%2Ftake.wav'}])}])
        self.send_error(404)


class MusicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FactoryStore(self.temp.name)
        self.pid = self.store.create('Livro', 'Pessoa', 'pt-BR')['id']
        FakeAce.submissions = []
        FakeAce.multipart = []
        self.ace = ThreadingHTTPServer(('127.0.0.1', 0), FakeAce)
        threading.Thread(target=self.ace.serve_forever, daemon=True).start()
        self.addCleanup(self.ace.server_close)
        self.addCleanup(self.ace.shutdown)
        self.studio = MusicStudio(self.store, AceStepClient(self.ace.server_port))

    def draft(self):
        return self.studio.save_draft(self.pid, {
            'title':'Abertura','prompt':'Piano e cordas','mood':'sereno',
            'structure':'intro, tema, final','lyrics':'','duration':30,
            'bpm':90,'key_scale':'C Major','time_signature':'4',
            'language':'pt','seed':42,'steps':8,'chapter':''})

    def test_local_draft_generate_poll_and_persist(self):
        track = self.draft()
        self.assertEqual(FakeAce.submissions, [])
        with self.assertRaises(MusicError):
            self.studio.generate(self.pid, track['id'], False)
        queued = self.studio.generate(self.pid, track['id'], True)
        self.assertEqual(queued['status'], 'queued')
        sent = FakeAce.submissions[0]
        self.assertIn('Mood: sereno', sent['prompt'])
        self.assertIn('Structure: intro, tema, final', sent['prompt'])
        self.assertEqual(sent['seed'], 42)
        self.assertFalse(sent['use_random_seed'])
        finished = self.studio.list(self.pid, refresh=True)[0]
        self.assertEqual(finished['status'], 'done')
        self.assertEqual(len(finished['takes']), 1)
        audio = self.studio.audio_file(self.pid, finished['takes'][0]['name'])
        with wave.open(str(audio), 'rb') as reader:
            self.assertEqual(reader.getnframes(), 800)
        self.assertEqual(MusicStudio(self.store, AceStepClient(self.ace.server_port)).list(self.pid)[0]['status'], 'done')

    def test_validation_and_fixed_origin(self):
        with self.assertRaises(MusicError):
            self.studio.save_draft(self.pid, {'title':'A','prompt':'X','duration':601})
        with self.assertRaises(MusicError):
            self.studio.client.download('https://example.com/music.wav')
        with self.assertRaises(MusicError):
            self.studio.client.download('//example.com/music.wav')

    def test_reference_persists_and_is_sent_only_on_generation(self):
        track = self.draft()
        reference = sample_wav()
        saved = self.studio.save_reference(self.pid, track['id'], reference, 'minha faixa.wav')
        self.assertEqual(saved['reference']['format'], 'wav')
        self.assertEqual(FakeAce.multipart, [])
        self.assertEqual(self.studio.reference_file(self.pid, track['id']).read_bytes(), reference)
        with self.assertRaises(MusicError):
            self.studio.reference_file(self.pid, track['id'], 'other.wav')
        restored = MusicStudio(self.store, AceStepClient(self.ace.server_port)).list(self.pid)[0]
        self.assertEqual(restored['reference']['label'], 'minha faixa.wav')
        self.studio.generate(self.pid, track['id'], True)
        content_type, body = FakeAce.multipart[0]
        self.assertIn('boundary=', content_type)
        self.assertIn(b'name="reference_audio"', body)
        self.assertIn(b'name="task_type"\r\n\r\ntext2music', body)
        self.assertIn(reference, body)
        self.assertEqual(self.studio.list(self.pid, refresh=True)[0]['status'], 'done')

    def test_reference_rejects_invalid_and_oversize_audio(self):
        track = self.draft()
        for data in (b'not music', b'RIFF' + b'0'*30, b'0' * 25_000_001):
            with self.assertRaises(MusicError):
                self.studio.save_reference(self.pid, track['id'], data, 'fake.wav')
        self.assertIsNone(self.studio.list(self.pid)[0].get('reference'))


if __name__ == '__main__':
    unittest.main()
