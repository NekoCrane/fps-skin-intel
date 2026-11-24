"""Regression checks for retained gun identities and unrestricted historical admission."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import sync_weapon_delta_bilibili as delta


class DeltaCollectionTests(unittest.TestCase):
    def test_real_official_name_formats(self):
        cases = [
            ('【AS Val突击步枪-拾花】', [('AS Val', '拾花', '突击步枪')]),
            ('PKM通用机枪-安魂', [('PKM', '安魂', '通用机枪')]),
            ('【725双管霰弹枪-微缩蒸汽】', [('725', '微缩蒸汽', '双管霰弹枪')]),
            ('典藏外观包含悬赏令-AS Val突击步枪', [('AS Val', '悬赏令', '突击步枪')]),
            ('内含黑鹰坠落-CAR-15突击步枪', [('CAR-15', '黑鹰坠落', '突击步枪')]),
            ('SCAR-H战斗步枪', []),
        ]
        for description, expected in cases:
            with self.subTest(description=description):
                self.assertEqual(delta.pairs({'description':description}), expected)

    def test_old_post_becomes_a_skin_and_news_without_year_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'data/weapon-skins').mkdir(parents=True)
            (root / 'local-only/weapon-skins').mkdir(parents=True)
            (root / 'docs').mkdir()
            record = {'bvid':'BV11jdbB8EsB', 'title':'【白鹰军团】系列外观实录',
                'description':'【K416突击步枪-白鹰军团】', 'publishedDate':'2020-01-01',
                'sourceUrl':'https://www.bilibili.com/video/BV11jdbB8EsB/',
                'coverUrl':'https://i1.hdslb.com/cover.jpg', 'ownerId':3494376565115651, 'duration':30}
            index = {'candidateBvids':[record['bvid']], 'pageCount':1,
                'uniqueEnumeratedVideos':1, 'keywords':['外观']}
            (root / 'data/weapon-skins/delta-bilibili-index.json').write_text(json.dumps(index), encoding='utf-8')
            (root / 'local-only/weapon-skins/delta-bilibili-review.json').write_text(json.dumps(
                {'metadataComplete':True, 'index':index, 'records':[record]}), encoding='utf-8')
            with patch.object(delta, 'ROOT', root), patch.object(delta.curated, 'archived_image', return_value={}):
                entries = delta.merge([], True)
                self.assertEqual(len(entries), 1)
                self.assertEqual(entries[0]['publishedDate'], '2020-01-01')
                self.assertEqual(delta.source_updates()[0]['associatedSkinIds'], [entries[0]['id']])
                # A clean checkout can rebuild from committed inventory without the local research cache.
                (root / 'local-only/weapon-skins/delta-bilibili-review.json').unlink()
                self.assertEqual(delta.merge([], True)[0]['id'], entries[0]['id'])


if __name__ == '__main__':
    unittest.main()
