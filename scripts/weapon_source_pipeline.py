"""Bounded public-source discovery, conditional HTTP caching and deterministic news extraction. No LLM calls."""
from datetime import datetime, timezone
import hashlib
import io
from html import unescape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urljoin, urlsplit, urlencode

SKIN_TERMS = re.compile(r'皮肤|外观|枪皮|刀皮|近战|通行证|典藏|捆绑包|武器套装|联动武器|英雄级|\b(?:skins?|cosmetics?|blueprints?|bundles?|blackcell|battle pass|heirloom|prestige|mastercraft|reactive)\b', re.I)


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def atomic(path, value, compact=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=None if compact else 2,
        separators=(',',':') if compact else None)+'\n', encoding='utf-8')
    os.replace(temporary, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def safe_url(url, host=None):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not parsed.username and not parsed.password and (not host or parsed.hostname == host)


class CachedHTTP:
    def __init__(self, root, offline=False):
        self.root, self.offline = Path(root), offline
        self.root.mkdir(parents=True, exist_ok=True)
        self.counts = {'requests':0, 'notModified':0, 'offlineHits':0, 'memoryHits':0, 'downloadedBytes':0}
        self.blocked = set()
        self.memory = {}

    def get(self, url):
        if not safe_url(url):
            raise ValueError('Public HTTPS URL required')
        if url in self.memory:
            self.counts['memoryHits'] += 1
            return self.memory[url]
        host = urlsplit(url).hostname
        key = hashlib.sha256(url.encode()).hexdigest()
        body_path, meta_path = self.root/(key+'.body'), self.root/(key+'.json')
        prior = read(meta_path, {})
        if self.offline:
            if prior.get('url') != url or not body_path.exists():
                raise RuntimeError('No offline source snapshot: '+url)
            body = body_path.read_bytes()
            if hashlib.sha256(body).hexdigest() != prior.get('sha256'):
                raise RuntimeError('Source cache hash mismatch')
            self.counts['offlineHits'] += 1
            self.memory[url] = body
            return body
        if host in self.blocked:
            raise RuntimeError('Source circuit breaker open: '+host)
        with tempfile.TemporaryDirectory(dir=self.root) as folder:
            folder = Path(folder)
            args = [shutil.which('curl.exe') or shutil.which('curl') or 'curl', '--silent', '--show-error',
                '--location', '--compressed', '--proto', '=https', '--proto-redir', '=https',
                '--connect-timeout','8', '--max-time','35', '--max-filesize','8000000',
                '--dump-header',str(folder/'headers'), '--output',str(folder/'body'),
                '--write-out','%{http_code}\n%{url_effective}', '--user-agent','fps-skin-intel-public-source/1.0']
            if os.name == 'nt':
                args.append('--ssl-revoke-best-effort')
            for field, header in [('etag','If-None-Match'), ('lastModified','If-Modified-Since')]:
                if prior.get(field) and body_path.exists():
                    args.extend(['--header', header+': '+prior[field]])
            result = subprocess.run([*args, url], capture_output=True, timeout=40)
            self.counts['requests'] += 1
            if result.returncode:
                raise RuntimeError(result.stderr.decode(errors='replace')[:250])
            status, final = [line.strip() for line in result.stdout.decode().strip().splitlines()]
            if not safe_url(final, host):
                raise ValueError('Unexpected source redirect host')
            if status in {'403','412','429'}:
                self.blocked.add(host)
            if status == '304':
                if not body_path.exists() or not prior:
                    raise RuntimeError('304 response without retained source')
                body = body_path.read_bytes()
                if hashlib.sha256(body).hexdigest() != prior.get('sha256'):
                    raise RuntimeError('304 source cache hash mismatch')
                prior['checkedAt'] = stamp()
                atomic(meta_path, prior)
                self.counts['notModified'] += 1
                self.memory[url] = body
                return body
            if status != '200':
                raise RuntimeError('HTTP '+status+': '+url)
            body = (folder/'body').read_bytes()
            if not body:
                raise RuntimeError('Empty source response')
            if host == 'api.bilibili.com':
                payload = json.loads(body)
                if payload.get('code') != 0:
                    self.blocked.add(host)
                    raise RuntimeError('Bilibili source refused: '+str(payload.get('code')))
            headers = (folder/'headers').read_text(encoding='latin-1')
            def header(name):
                values = re.findall(r'^'+name+r':\s*([^\r\n]+)', headers, re.I|re.M)
                return values[-1] if values else None
            meta = {'url':url, 'sha256':hashlib.sha256(body).hexdigest(), 'retrievedAt':stamp(),
                'checkedAt':stamp(), 'etag':header('etag'), 'lastModified':header('last-modified')}
            body_path.write_bytes(body)
            atomic(meta_path, meta)
            self.counts['downloadedBytes'] += len(body)
            self.memory[url] = body
            return body

    def json(self, url):
        return json.loads(self.get(url))


class Page(HTMLParser):
    def __init__(self, body):
        super().__init__(convert_charrefs=True)
        self.links, self.meta, self.parts, self.heading = [], {}, [], []
        self.active_link = None
        self.in_h1 = 0
        self.in_heading = 0
        self.sections = []
        self.skip = 0
        self.feed(body)
        self.text = ' '.join(' '.join(self.parts).split())
        self.title = ' '.join(' '.join(self.heading).split())

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in {'script','style'}:
            self.skip += 1
        if tag == 'meta':
            self.meta[(attrs.get('property') or attrs.get('name') or '').lower()] = attrs.get('content','')
        if tag == 'h1':
            self.in_h1 += 1
        if tag in {'h2','h3','h4'}:
            self.in_heading += 1
        if tag == 'a':
            self.active_link = {'url':attrs.get('href',''), 'parts':[]}

    def handle_endtag(self, tag):
        if tag in {'script','style'}:
            self.skip = max(0, self.skip-1)
        if tag == 'h1':
            self.in_h1 = max(0,self.in_h1-1)
        if tag in {'h2','h3','h4'}:
            self.in_heading = max(0,self.in_heading-1)
        if tag == 'a' and self.active_link:
            self.links.append((self.active_link['url'], ' '.join(self.active_link['parts'])))
            self.active_link = None

    def handle_data(self, value):
        if not self.skip:
            self.parts.append(value)
            if self.in_h1:
                self.heading.append(value)
            if self.in_heading:
                self.sections.append(value)
            if self.active_link:
                self.active_link['parts'].append(value)


def article_news(raw, url, provider):
    if not safe_url(url, urlsplit(provider['url']).hostname):
        raise ValueError('Unapproved publisher host')
    page = Page(raw)
    title = page.title or page.meta.get('og:title','')
    if provider.get('includeTitlePattern') and not re.search(provider['includeTitlePattern'],title,re.I):
        return None
    if provider.get('excludeTitlePattern') and re.search(provider['excludeTitlePattern'],title,re.I):
        return None
    direct = title+' '+page.meta.get('description','')+' '+page.meta.get('og:description','')
    section_evidence = re.search(r'event|season|活动|赛季',title,re.I) and SKIN_TERMS.search(' '.join(page.sections))
    if not title or not (SKIN_TERMS.search(direct) or section_evidence):
        return None
    dates = [page.meta.get('article:published_time',''), page.meta.get('date',''),
        *re.findall(r'"datePublished"\s*:\s*"([^"\\]+)', raw)]
    date = next((value[:10] for value in dates if re.match(r'^\d{4}-\d{2}-\d{2}', value)), '')
    # Some official news pages expose a readable date, without metadata.
    if not date:
        match = re.search(r'\b([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})\b', page.text[:6000])
        if match:
            try:
                date = datetime.strptime(match[1], '%B %d, %Y').date().isoformat()
            except ValueError:
                pass
    image = urljoin(url, page.meta.get('og:image',''))
    if not date or not safe_url(image) or not page.meta.get('og:image'):
        raise ValueError('Cosmetic article lacks a confirmed date or preview image: '+url)
    return {'id':provider['id']+'-'+hashlib.sha256(url.encode()).hexdigest()[:16],
        'title':unescape(title), 'publishedDate':date, 'sourceUrl':url, 'sourceName':provider['name'],
        'sourceType':'official', 'thumbnailUrl':image, 'publicationStatus':'ready', 'associatedSkinIds':[]}


def html_news(http, provider, full=False):
    host = urlsplit(provider['url']).hostname
    index = Page(http.get(provider['url']).decode('utf-8', errors='replace'))
    candidates = list(dict.fromkeys(urljoin(provider['url'], href).split('#')[0] for href, _ in index.links
        if provider['articlePath'] in urljoin(provider['url'], href)
        and safe_url(urljoin(provider['url'], href), host)))
    candidates = [url for url in candidates if url.rstrip('/') != provider['url'].rstrip('/')]
    if not candidates:
        raise ValueError('No article links found; source layout may have changed')
    result, issues = [], []
    for url in candidates[:provider.get('limit',12)*(3 if full else 1)]:
        body = ''
        try:
            body = http.get(url).decode('utf-8', errors='replace')
            item = article_news(body, url, provider)
            if item:
                result.append(item)
        except Exception as error:
            issue = {'reason':'article_fetch_or_parse_failed','sourceUrl':url,'detail':str(error),'requiresModel':isinstance(error,ValueError)}
            if body and isinstance(error,ValueError):
                text = Page(body).text
                issue.update(fingerprint=digest(text),excerpt=text[:1200])
            issues.append(issue)
            if host in http.blocked:
                break
    return result, issues


def discover_bilibili(http, provider, known, full=False):
    rows, completed = {}, False
    total = None
    for page in range(1, 21 if full else 3):
        url = 'https://api.bilibili.com/x/space/arc/search?'+urlencode(
            {'mid':provider['ownerId'],'ps':50,'pn':page,'order':'pubdate'})
        payload = http.json(url)
        if payload.get('code') != 0:
            http.blocked.add('api.bilibili.com')
            raise RuntimeError('Bilibili account discovery refused: '+str(payload.get('code')))
        data = payload.get('data',{})
        videos = data.get('list',{}).get('vlist',[])
        total = data.get('page',{}).get('count')
        for row in videos:
            if not re.fullmatch(r'BV[0-9A-Za-z]{10}',row.get('bvid','')) or int(row.get('mid',0)) != provider['ownerId']:
                raise ValueError('Account result author or BVID mismatch')
            rows[row['bvid']] = row
        if not videos or len(rows) >= (total or float('inf')) or (not full and any(r['bvid'] in known for r in videos)):
            completed = True
            break
    return list(rows.values()), {'pagesRead':page,'reportedTotal':total,'scanBoundaryReached':completed}


def merge_news(*collections):
    by_url = {}
    for collection in collections:
        for item in collection:
            if item.get('publicationStatus') == 'ready' and item.get('sourceType') == 'official' and safe_url(item.get('sourceUrl','')):
                by_url[item['sourceUrl']] = item
    return sorted(by_url.values(),key=lambda item:(item['publishedDate'],item['id']),reverse=True)[:30]


def verify_news_images(http, items):
    from PIL import Image
    accepted, issues = [], []
    for item in items:
        try:
            body = http.get(item['thumbnailUrl'])
            sha = hashlib.sha256(body).hexdigest()
            proof = item.get('imageProof',{})
            if proof.get('sha256') != sha:
                with Image.open(io.BytesIO(body)) as image:
                    image.verify()
                with Image.open(io.BytesIO(body)) as image:
                    if image.width<120 or image.height<65:
                        raise ValueError('News image is too small for a meaningful preview')
                    proof = {'sha256':sha,'width':image.width,'height':image.height,'format':image.format}
            accepted.append({**item,'imageProof':proof})
        except Exception as error:
            issues.append({'reason':'news_image_failed','sourceUrl':item['sourceUrl'],'detail':str(error)[:350], 'requiresModel':False})
    return accepted,issues


def reconcile_queue(previous, issues, run_id, max_items=5):
    prior = {item['id']:item for item in previous}
    batch = []
    for issue in issues:
        key = digest([issue['game'],issue['reason'],issue.get('sourceUrl'),issue.get('fingerprint')])[:24]
        old = prior.get(key)
        item = {**(old or {}), **issue, 'id':key, 'status':old.get('status','pending') if old else 'pending',
            'firstSeenAt':old.get('firstSeenAt',stamp()) if old else stamp(), 'lastSeenAt':stamp(), 'lastRunId':run_id}
        prior[key] = item
    ordered = sorted(prior.values(),key=lambda item:item.get('publishedDate',item['firstSeenAt']),reverse=True)
    ordered.sort(key=lambda item:item['reason']!='unparsed_official_change')
    for item in ordered:
        if item['status']=='pending' and not item.get('offeredToModelAt') and item.get('requiresModel') and len(batch)<max_items:
            item['offeredToModelAt']=stamp()
            batch.append(item)
    return list(prior.values()), batch
