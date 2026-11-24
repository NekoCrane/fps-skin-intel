import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import run_weapon_weekly as weekly
import weapon_source_pipeline as source


class SourceTests(unittest.TestCase):
    def test_windows_crlf_and_304_reuses_verified_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            def first(args, **kwargs):
                Path(args[args.index('--output')+1]).write_bytes(b'{"value":1}')
                Path(args[args.index('--dump-header')+1]).write_bytes(b'HTTP/1.1 200 OK\r\nETag: "revision1"\r\n')
                return subprocess.CompletedProcess(args,0,b'200\r\nhttps://example.com/data',b'')
            with patch.object(source.subprocess,'run',side_effect=first):
                self.assertEqual(source.CachedHTTP(folder).json('https://example.com/data'),{'value':1})
            def unchanged(args, **kwargs):
                self.assertIn('If-None-Match: "revision1"',args)
                return subprocess.CompletedProcess(args,0,b'304\r\nhttps://example.com/data',b'')
            with patch.object(source.subprocess,'run',side_effect=unchanged):
                client = source.CachedHTTP(folder)
                self.assertEqual(client.json('https://example.com/data'),{'value':1})
                self.assertEqual(client.counts['notModified'],1)
                client.get('https://example.com/data')
                self.assertEqual(client.counts['requests'],1)

    def test_refused_bilibili_json_does_not_replace_successful_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            def refused(args, **kwargs):
                Path(args[args.index('--output')+1]).write_bytes(b'{"code":-799}')
                return subprocess.CompletedProcess(args,0,b'200\nhttps://api.bilibili.com/x/space/arc/search',b'')
            with patch.object(source.subprocess,'run',side_effect=refused) as request:
                client=source.CachedHTTP(folder)
                with self.assertRaisesRegex(RuntimeError,'-799'):
                    client.get('https://api.bilibili.com/x/space/arc/search')
                with self.assertRaisesRegex(RuntimeError,'circuit breaker'):
                    client.get('https://api.bilibili.com/x/space/arc/search')
                self.assertEqual(request.call_count,1)
            self.assertFalse(list(Path(folder).glob('*.body')))

    def test_news_requires_article_evidence_not_navigation_skin_links(self):
        provider={'id':'test','url':'https://example.com/news','name':'Publisher'}
        body='<meta property="og:image" content="https://example.com/hero.png"><meta property="article:published_time" content="2026-11-20T10:00:00Z">'
        self.assertIsNone(source.article_news(body+'<nav>Weapon skins</nav><h1>Anti-cheat update</h1>','https://example.com/news/a',provider))
        news=source.article_news(body+'<h1>Season 1</h1><h2>Weapon Blueprints</h2>','https://example.com/news/a',provider)
        self.assertEqual(news['publishedDate'],'2026-11-20')
        with self.assertRaises(ValueError):
            source.article_news(body+'<h1>Skins</h1>','https://evil.example/news/a',provider)

    def test_unchanged_exceptions_are_not_sent_to_model_again(self):
        issues=[{'game':'delta-force','reason':'weapon_mapping_needed','sourceUrl':'https://example.com/'+str(i),
            'fingerprint':str(i),'requiresModel':True} for i in range(8)]
        queue,batch=source.reconcile_queue([],issues,'run1',5)
        self.assertEqual(len(batch),5)
        again,batch=source.reconcile_queue(queue,issues,'run2',5)
        self.assertEqual(len(batch),3, 'Previously unoffered overflow must not starve')
        self.assertEqual(len(again),8)
        _,batch=source.reconcile_queue(again,issues,'run3',5)
        self.assertEqual(len(batch),0)

    def test_news_merges_preserve_old_entries_and_new_versions(self):
        old={'id':'a','sourceUrl':'https://example.com/a','publishedDate':'2020-01-01','sourceType':'official','publicationStatus':'ready','title':'old'}
        updated={**old,'title':'corrected'}
        result=source.merge_news([old],[updated])
        self.assertEqual(result,[updated])

    def test_failed_game_restores_original_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'game.json';path.write_bytes(b'previous successful snapshot')
            saved={path:path.read_bytes()};path.write_bytes(b'partial failed snapshot')
            weekly.rollback(saved)
            self.assertEqual(path.read_bytes(),b'previous successful snapshot')

    def test_offline_mode_removes_live_refresh_flags(self):
        self.assertEqual(weekly.collector_args(['sync_weapon_cfhd.py','--refresh'],True),['sync_weapon_cfhd.py','--offline'])

    def test_plan_has_no_working_state_or_network_writes(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(weekly,'WORK',Path(directory)/'not-created'):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(weekly.main(['--plan','--games','delta-force']),0)
            self.assertEqual(json.loads(output.getvalue())['llmCalls'],0)
            self.assertFalse(weekly.WORK.exists())


if __name__=='__main__':
    unittest.main()
