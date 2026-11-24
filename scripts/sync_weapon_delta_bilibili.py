#!/usr/bin/env python3
"""Map explicitly named official-account weapon appearances into the existing catalogue."""
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sync_weapon_curated as curated

ROOT = Path(__file__).resolve().parent.parent
TYPES = ['精确射手步枪','紧凑突击步枪','双管霰弹枪','突击步枪','战斗步枪','冲锋枪','轻机枪','通用机枪','狙击步枪','射手步枪','霰弹枪','手枪','左轮']
TYPED = re.compile(r'^(.+?)(' + '|'.join(TYPES) + r')[-－—·](.+)$')
REVERSED = re.compile(r'^(.+?)[-－—·](.+?)(' + '|'.join(TYPES) + r')$')
BARE = {'93R':'手枪', 'G18':'手枪', '.357':'手枪', '杠杆式步枪':'步枪'}
RETURNED_MELEE = {
    'BV1D2MU6yEdv': ['黑海', '北极星'],
    'BV1oUBqBZEEc': ['龙牙', '赤枭', '怜悯', '影锋'],
}


def identity(weapon, name):
    return re.sub(r'\s+', '', weapon + '|' + name).casefold()


def clean_pair(weapon, name, category):
    weapon = weapon.strip(' -－—·')
    aliases = {'asval':'AS Val', 'ash-12':'ASh-12', 'mini14':'Mini-14'}
    weapon = aliases.get(re.sub(r'\s+', '', weapon).casefold(), weapon)
    name = re.sub(r'^(?:典藏主题外观包含|典藏外观包含|内含)', '', name).strip()
    # Avoid reading a base name such as “SCAR-H战斗步枪” as a skin SCAR for a weapon H.
    model = weapon in {'勇士', '腾龙', '杠杆式步枪', '.357', '725'} or (
        bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9 .-]{1,19}', weapon)) and
        any(char.isalpha() for char in weapon))
    return (weapon, name, category) if name and model else None


def pairs(record):
    found = []
    # Official bundle lists also contain unbracketed weapon names after “系列包含：”.
    for text in re.split(r'[，、。\n：:【】「」]', record['description']):
        text = text.strip()
        match = TYPED.fullmatch(text)
        if match:
            weapon, category, name = match.groups()
            for suffix in TYPES:
                weapon = weapon.removesuffix(suffix)
            found.append((weapon.strip(), name.strip(), category))
            continue
        match = REVERSED.fullmatch(text)
        if match:
            name, weapon, category = match.groups()
            found.append((weapon.strip(), name.strip(), category))
            continue
        for weapon, category in BARE.items():
            if text.startswith(weapon + '-'):
                found.append((weapon, text[len(weapon)+1:].strip(), category))
    return list(dict.fromkeys(pair for weapon, name, category in found
        if (pair := clean_pair(weapon, name, category))))


def merge(entries, offline):
    report_path = ROOT / 'local-only/weapon-skins/delta-bilibili-review.json'
    inventory_path = ROOT / 'data/weapon-skins/delta-bilibili-inventory.json'
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding='utf-8'))
    elif inventory_path.exists():
        saved = json.loads(inventory_path.read_text(encoding='utf-8'))
        report = {'metadataComplete':True, 'records':saved['records'],
            'index':{'uniqueEnumeratedVideos':saved['indexedAccountVideos']}}
    else:
        return entries
    if not report['metadataComplete']:
        raise ValueError('Incomplete official account metadata; retain previous catalogue')
    today = datetime.now(timezone.utc).date()
    account_index = json.loads((ROOT / 'data/weapon-skins/delta-bilibili-index.json').read_text(encoding='utf-8'))
    if set(account_index['candidateBvids']) != {row['bvid'] for row in report['records']}:
        raise ValueError('Account index changed; refresh all candidate metadata before replacing the catalogue')
    by_key = {}
    for entry in entries:
        base_name = entry['name'].split(' · ')[-1]
        by_key[identity(entry['weaponName'], base_name)] = entry
    inventory = []
    for record in report['records']:
        valid_date = bool(re.fullmatch(r'\d{4}-\d{2}-\d{2}', record['publishedDate'])) and record['publishedDate'] <= today.isoformat()
        named = pairs(record) if valid_date else []
        # A named melee showcase can identify the skin while its exact model remains unverified.
        melee_names = []
        if valid_date and '近战武器' in record['title']:
            title_names = re.findall(r'【([^】]+)】|「([^」]+)」', record['title'])
            if title_names:
                name = next(part for part in title_names[0] if part)
                melee_names.append(name)
        if valid_date:
            melee_names.extend(RETURNED_MELEE.get(record['bvid'], []))
        for name in melee_names:
            existing = next((entry for entry in entries if entry['weaponType'] == 'melee'
                and entry['name'].split(' · ')[-1] == name), None)
            named.append((existing['weaponName'] if existing else '近战武器', name, '近战武器'))
        linked = []
        for weapon, name, category in named:
            key = identity(weapon, name)
            if key in by_key:
                linked.append(by_key[key]['id'])
                continue
            digest = hashlib.sha256(key.encode()).hexdigest()[:16]
            bvid = record['bvid']
            url = record['sourceUrl']
            entry = curated.skin('delta-official-' + digest, 'delta-force', weapon + ' · ' + name,
                'delta-' + weapon, weapon, 'melee' if category == '近战武器' else 'gun', category,
                url, '三角洲行动官方 · 哔哩哔哩',
                '官方投稿简介明确列出的武器外观。名称与武器对应依据官方文字；预览使用该投稿的宣传封面。',
                region='国服')
            entry.update(firstSeenAt=today.isoformat(), publishedDate=record['publishedDate'],
                reviewedAt=today.isoformat(), sourceId=bvid)
            entry['image'] = curated.archived_image('delta-account-' + bvid + '.jpg',
                record['coverUrl'], url, '三角洲行动官方', kind='poster',
                caption='官方投稿封面，可能包含同系列其他物品；并非单件独立渲染。')
            entry['image']['retrievedAt'] = today.isoformat()
            # Raw originals remain archived; official CDN delivery keeps Pages below its existing capacity limit.
            entry['image']['url'] = record['coverUrl']
            entry['media'] = [{
                'id':'bilibili-' + bvid, 'provider':'bilibili', 'kind':'showcase',
                'url':url, 'videoId':bvid, 'title':record['title'], 'sourcePageUrl':url,
                'sourceName':'三角洲行动官方', 'sourceType':'official',
                'reviewStatus':'source_confirmed', 'reviewedAt':today.isoformat(),
                'reviewNote':'已核官方作者、标题与简介的具名对应；动作画面及时间段尚待核实。',
                'duration':record['duration'],
            }]
            entry['sourceEvidence'] = {'basis':'official_video_description',
                'ownerId':record['ownerId'], 'publishedDate':record['publishedDate'],
                'weaponName':weapon, 'skinName':name}
            entries.append(entry)
            by_key[key] = entry
            linked.append(entry['id'])
        linked.extend(entry['id'] for entry in entries if entry.get('sourceId') == record['bvid'])
        linked = list(dict.fromkeys(linked))
        disposition = 'invalid_publication_date' if not valid_date else (
            'named_weapons_mapped' if linked else 'needs_item_mapping_or_non_weapon')
        inventory.append({**record, 'disposition':disposition, 'skinIds':linked})
    curated.atomic_json(inventory_path,
        {'schemaVersion':1, 'collectionScope':'all_available_history', 'indexedAccountVideos':report['index']['uniqueEnumeratedVideos'],
         'candidateCount':len(inventory), 'records':inventory,
         'note':'全部标题候选均已核作者与简介。未具名单品的通行证和典藏外观仍需画面或其他官方资料映射。'})
    labels = {'named_weapons_mapped':'已关联目录', 'invalid_publication_date':'投稿日期待核',
        'needs_item_mapping_or_non_weapon':'待映射或非武器'}
    names = {entry['id']: entry['name'] for entry in entries}
    lines = ['# 三角洲行动官方 B 站外观词条盘点', '',
        f'核查日期：{today.isoformat()}。官方账号：3494376565115651。', '',
        f"逐页盘点{account_index['pageCount']}页，共{account_index['uniqueEnumeratedVideos']}条不重复公开投稿；按{'、'.join(account_index['keywords'])}筛出{len(inventory)}条标题候选，均核实官方作者与简介。", '',
        '收录范围：全部可核实历史资料，不设近一年限制。系列按“基础武器＋外观名”拆分并去重；返场不重复计数。', '',
        '封面为官方投稿宣传图，可能包含系列内其他物品。动作画面和时间段尚未逐帧核验；标题没有关键词的视频仍可能包含遗漏。待映射或非武器不等于新增皮肤，角色、挂饰和活动介绍也会命中关键词。', '',
        '| 投稿日期 | 官方词条与原片 | 处理状态 | 已关联武器外观 |',
        '|---|---|---|---|']
    for row in sorted(inventory, key=lambda row:row['publishedDate'], reverse=True):
        title = row['title'].replace('|', '／').replace('\n', ' ')
        mapped = '；'.join(names[identifier] for identifier in row['skinIds']).replace('|', '／') or '—'
        lines.append(f"| {row['publishedDate']} | [{title}]({row['sourceUrl']}) | {labels[row['disposition']]} | {mapped} |")
    (ROOT / 'docs/delta-bilibili-inventory.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
    return entries


def source_updates():
    """Keep official news visible even when individual weapon mapping is pending."""
    path = ROOT / 'data/weapon-skins/delta-bilibili-inventory.json'
    if not path.exists():
        return []
    inventory = json.loads(path.read_text(encoding='utf-8'))
    records = sorted(inventory['records'], key=lambda row:row['publishedDate'], reverse=True)
    result = []
    for row in records:
        if row['ownerId'] != 3494376565115651 or not re.search(r'外观|近战|通行证|枪皮|刀皮', row['title']):
            continue
        if re.search(r'挂饰|主题曲', row['title']):
            continue
        result.append({'id':'delta-news-'+row['bvid'], 'title':row['title'],
            'publishedDate':row['publishedDate'], 'sourceUrl':row['sourceUrl'],
            'sourceName':'三角洲行动官方 · 哔哩哔哩', 'sourceType':'official',
            'thumbnailUrl':row['coverUrl'], 'publicationStatus':'ready',
            'associatedSkinIds':row['skinIds']})
        if len(result) == 30:
            break
    return result
