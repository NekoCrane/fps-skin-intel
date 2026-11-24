#!/usr/bin/env python3
"""One entry point for skin collectors, official news, review queue, checks and publishing. No LLM SDK or API."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

import weapon_source_pipeline as sources

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data/weapon-skins'
WORK = ROOT / 'local-only/weapon-skins/weekly'
REGISTRY = DATA / 'source-registry.json'
VOLATILE = {'retrievedAt','reviewedAt','updatedAt','checkedAt'}


def semantic(value):
    if isinstance(value, dict):
        return {key:semantic(item) for key,item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [semantic(item) for item in value]
    return value


def changes(before, after):
    old = {item['id']:item for item in before.get('skins',[])}
    new = {item['id']:item for item in after.get('skins',[])}
    return {'added':len(new.keys()-old.keys()), 'removed':len(old.keys()-new.keys()),
        'modified':sum(semantic(old[key]) != semantic(new[key]) for key in old.keys() & new.keys()),
        'newsChanged':semantic(before.get('sourceUpdates',[])) != semantic(after.get('sourceUpdates',[]))}


def collector_args(job, offline=False, full=False, overlap_days=14):
    result = list(job)
    if '--refresh-recent-days' in result:
        result[result.index('--refresh-recent-days')+1] = str(overlap_days)
    if offline:
        result = [item for item in result if item not in {'--refresh','--resume'}]
        result.append('--offline')
    elif full:
        result = [item for item in result if item != '--resume']
        if result[0] == 'research_weapon_delta_bilibili.py':
            result = [result[0], '--refresh']
    return result


def execute(args, label, folder, timeout=1800):
    log = folder / (re.sub(r'[^a-zA-Z0-9_.-]','_',label)+'.log')
    started = time.monotonic()
    with log.open('wb') as output:
        try:
            process = subprocess.run(args,cwd=ROOT,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
            code = process.returncode
        except subprocess.TimeoutExpired:
            code = 124
    return {'command':args, 'exitCode':code, 'seconds':round(time.monotonic()-started,2),
        'log':str(log.relative_to(ROOT))}


def transaction_files(game):
    paths = [DATA/(game+'.json')]
    if game == 'delta-force':
        paths += [DATA/'delta-bilibili-index.json', DATA/'delta-bilibili-inventory.json', ROOT/'docs/delta-bilibili-inventory.md']
    return paths


def rollback(saved):
    for path, content in saved.items():
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_bytes(content)


def news_discovery(http, registry, games, full):
    news, issues, reports = {game:[] for game in games}, [], []
    seen_path = WORK/'discovery-state.json'
    state = sources.read(seen_path,{})
    health_path = WORK/'source-health.json'
    health = sources.read(health_path,{})
    for provider in registry['news']:
        if provider['game'] not in games:
            continue
        report = {'id':provider['id'], 'game':provider['game'], 'sourceUrl':provider['url'],
            'attemptedAt':sources.stamp(), 'lastSuccessfulAt':health.get(provider['id'],{}).get('lastSuccessfulAt')}
        try:
            if provider['type'] == 'monitor':
                page = sources.Page(http.get(provider['url']).decode('utf-8',errors='replace'))
                if len(page.text)<100:
                    raise ValueError('Official activity page returned no usable public text')
                fingerprint = sources.digest(page.text)
                prior = state.get(provider['id'],{})
                changed = prior.get('fingerprint') != fingerprint
                if changed:
                    issues.append({'game':provider['game'],'reason':'unparsed_official_change','sourceUrl':provider['url'],
                        'fingerprint':fingerprint,'excerpt':page.text[:1200], 'requiresModel':True,
                        'detail':'Official page changed, but no reliable article date/schema was extracted; do not invent a publication date.'})
                if not http.offline:
                    state[provider['id']]={'fingerprint':fingerprint,'checkedAt':sources.stamp()}
                report.update(status='changed_needs_review' if changed else 'success',changed=changed)
            elif provider['type'] == 'html':
                items, errors = sources.html_news(http,provider,full)
                news[provider['game']].extend(items)
                issues.extend({**error,'game':provider['game']} for error in errors)
                report.update(status='partial' if errors else 'success',news=len(items),errors=len(errors))
            else:
                known = set(state.get(provider['id'],[]))
                if provider['game'] == 'delta-force':
                    index_path = DATA/'delta-bilibili-index.json'
                    index = sources.read(index_path)
                    known.update(index['candidateBvids'])
                rows, scope = sources.discover_bilibili(http,provider,known,full)
                if provider['game'] == 'delta-force':
                    index['candidateBvids'] = list(dict.fromkeys([*index['candidateBvids'],*(r['bvid'] for r in rows)]))
                    index['candidateCount'] = len(index['candidateBvids'])
                    index['lastDiscoveryAt'] = sources.stamp()
                    index['lastDiscovery'] = scope
                    sources.atomic(index_path,index)
                else:
                    for row in rows:
                        # The list is lightweight discovery; metadata supplies the description before filtering.
                        payload = http.json('https://api.bilibili.com/x/web-interface/view?bvid='+row['bvid'])
                        if payload.get('code') != 0:
                            raise RuntimeError('Bilibili view refused: '+str(payload.get('code')))
                        video = payload['data']
                        if video['owner']['mid'] != provider['ownerId'] or video['owner']['name'] != provider['ownerName']:
                            raise ValueError('Official video author mismatch')
                        content = video['title']+' '+video.get('desc','')
                        if provider.get('includeContentPattern') and not re.search(provider['includeContentPattern'],content,re.I):
                            continue
                        if sources.SKIN_TERMS.search(content):
                            news[provider['game']].append({'id':provider['id']+'-'+row['bvid'],
                                'title':video['title'], 'publishedDate':datetime.fromtimestamp(video['pubdate'],timezone.utc).date().isoformat(),
                                'sourceUrl':'https://www.bilibili.com/video/'+row['bvid']+'/', 'sourceName':provider['name'],
                                'sourceType':'official','thumbnailUrl':video['pic'].replace('http://','https://'),
                                'publicationStatus':'ready','associatedSkinIds':[]})
                        if not http.offline:
                            time.sleep(1.1)
                state[provider['id']] = list(dict.fromkeys([*(r['bvid'] for r in rows),*state.get(provider['id'],[])]))[:2000]
                report.update(status='success' if scope['scanBoundaryReached'] else 'partial', **scope)
            if not http.offline:
                report['checkedAt'] = sources.stamp()
            else:
                report['status'] = 'offline_cache'
        except Exception as error:
            report.update(status='offline_unavailable' if http.offline else 'failed', error=str(error))
            if not http.offline:
                issues.append({'game':provider['game'],'sourceUrl':provider['url'],'reason':'discovery_failed',
                    'detail':str(error)[:500], 'requiresModel':False})
        if not http.offline:
            if report['status']=='success':
                report['lastSuccessfulAt']=sources.stamp()
            health[provider['id']]=report
        reports.append(report)
    if not http.offline:
        sources.atomic(seen_path,state)
        sources.atomic(health_path,health)
    return news, issues, reports


def unmapped_delta():
    inventory = sources.read(DATA/'delta-bilibili-inventory.json',{'records':[]})
    issues = []
    for row in sorted(inventory['records'], key=lambda row:row['publishedDate'],reverse=True):
        if row['disposition'] != 'needs_item_mapping_or_non_weapon' or not re.search(r'枪|刀|武器|通行证',row['title']+' '+row['description']):
            continue
        issues.append({'game':'delta-force','reason':'weapon_mapping_needed','sourceUrl':row['sourceUrl'],
            'title':row['title'],'publishedDate':row['publishedDate'], 'excerpt':row['description'][:1200],
            'fingerprint':sources.digest([row['title'],row['description']]), 'requiresModel':True})
    return issues


def git(args, cwd=ROOT):
    result = subprocess.run(['git',*args],cwd=cwd,capture_output=True,text=True,encoding='utf-8',timeout=300)
    if result.returncode:
        raise RuntimeError('git '+args[0]+' failed: '+result.stderr[-500:])
    return result.stdout.strip()


def publish():
    public = ROOT.parent/'fps-skin-publish'
    if public.resolve() == ROOT.resolve() or not (public/'.git').exists():
        raise ValueError('Expected separate fps-skin-publish checkout')
    if git(['branch','--show-current']) != 'weapon-skins' or git(['branch','--show-current'],public) != 'gh-pages':
        raise ValueError('Unexpected publishing branches')
    for checkout in [ROOT,public]:
        if git(['remote','get-url','origin'],checkout).rstrip('/').removesuffix('.git') != 'https://github.com/NekoCrane/fps-skin-intel':
            raise ValueError('Unexpected publishing remote')
    if git(['status','--porcelain'],public):
        raise ValueError('Publishing checkout contains uncommitted changes')
    paths = ['data/weapon-skins','assets/weapon-skins','docs/delta-bilibili-inventory.md']
    modified = git(['diff','--name-only','HEAD']).splitlines()+git(['ls-files','--others','--exclude-standard']).splitlines()
    if any(not any(name==prefix or name.startswith(prefix+'/') for prefix in paths) for name in modified):
        raise ValueError('Commit implementation changes before running automatic publication')
    git(['pull','--ff-only','origin','gh-pages'],public)
    # Source commits are explicitly scoped to collector outputs, never local logs or unrelated edits.
    git(['add','--',*paths])
    if git(['diff','--cached','--name-only']):
        staged = git(['diff','--cached','--name-only']).splitlines()
        if any(not any(name==prefix or name.startswith(prefix+'/') for prefix in paths) for name in staged):
            raise ValueError('Unrelated staged files; source publication stopped')
        git(['commit','-m','Update weapon catalogues and official news'])
    git(['push','origin','weapon-skins'])
    output = ROOT/'_site'
    files = ['index.html','weapon-skins.js','weapon-skins.css','data/weapon-skins/manifest.json']
    files += [p.relative_to(output).as_posix() for p in (output/'data/weapon-skins').glob('*.json.gz')]
    files += [p.relative_to(output).as_posix() for p in (output/'assets/weapon-skins').rglob('*') if p.is_file()]
    changed = []
    for name in files:
        source, target = output/name, public/name
        if not source.is_file() or not target.resolve().is_relative_to(public.resolve()):
            raise ValueError('Invalid artifact path')
        if not target.exists() or source.read_bytes() != target.read_bytes():
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
            changed.append(name)
    wanted = set(files)
    for name in git(['ls-files','assets/weapon-skins'],public).splitlines():
        if name not in wanted:
            target = (public/name).resolve()
            if not target.is_relative_to((public/'assets/weapon-skins').resolve()):
                raise ValueError('Invalid stale weapon artifact path')
            target.unlink(missing_ok=True)
            changed.append(name)
    for offset in range(0,len(changed),40):
        git(['add','-A','--',*changed[offset:offset+40]],public)
    if changed:
        git(['commit','-m','Publish refreshed weapon catalogues and official news'],public)
        git(['push','origin','gh-pages'],public)
    return {'sourceCommit':git(['rev-parse','HEAD']), 'publicCommit':git(['rev-parse','HEAD'],public),
        'changedFiles':len(changed), 'onlineVerification':'pending_pages_deployment'}


def verify_publication(games):
    import sync_weapon_curated as curated
    base = 'https://nekocrane.github.io/fps-skin-intel/'
    expected = (ROOT/'_site/data/weapon-skins/manifest.json').read_bytes()
    last_error = None
    for attempt in range(8):
        try:
            suffix = '?weekly='+str(time.time_ns())
            if curated.request(base+'data/weapon-skins/manifest.json'+suffix) != expected:
                raise ValueError('The new manifest has not reached Pages yet')
            for game in games:
                path = 'data/weapon-skins/'+game+'.json.gz'
                if curated.request(base+path+suffix) != (ROOT/'_site'/path).read_bytes():
                    raise ValueError('The live catalogue still differs: '+game)
            return {'status':'verified','checkedAt':sources.stamp(),'attempts':attempt+1}
        except Exception as error:
            last_error = error
            if attempt<7:
                time.sleep(15)
    raise RuntimeError('Pushed commits but online verification did not finish: '+str(last_error))


def main(argv=None):
    registry = sources.read(REGISTRY)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--games',nargs='+',choices=list(registry['jobs']),default=list(registry['jobs']))
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--full',action='store_true')
    parser.add_argument('--plan',action='store_true')
    parser.add_argument('--news-only',action='store_true')
    parser.add_argument('--skip-build',action='store_true')
    parser.add_argument('--publish',action='store_true')
    args = parser.parse_args(argv)
    if args.publish and (args.offline or args.skip_build or args.plan):
        parser.error('--publish requires live collection and a validated build')
    if args.news_only and 'delta-force' in args.games:
        parser.error('--news-only requires explicit games excluding delta-force; use its metadata collector for discovery')
    if args.publish and git(['status','--porcelain']):
        parser.error('Start publication from a clean source checkout; review and commit existing changes first')
    now = datetime.now().astimezone()
    full = args.full or (now.weekday()==0 and now.day<=7)
    if args.plan:
        print(json.dumps({'games':args.games,'full':full,'newsOnly':args.news_only,
            'jobs':{game:[collector_args(job,args.offline,full,registry['overlapDays']) for job in registry['jobs'][game]] for game in args.games},
            'llmCalls':0},ensure_ascii=False,indent=2))
        return 0
    WORK.mkdir(parents=True,exist_ok=True)
    lock = WORK/'running.lock'
    try:
        descriptor = os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        print('Another run or an uncleared interrupted-run lock exists: '+str(lock))
        return 2
    os.write(descriptor,str(os.getpid()).encode());os.close(descriptor)
    report = {'runId':now.strftime('%Y%m%d-%H%M%S'),'startedAt':sources.stamp(), 'offline':args.offline,
        'full':full,'llmCalls':0,'games':{},'checks':[]}
    folder = WORK/report['runId'];folder.mkdir()
    before = {game:sources.read(DATA/(game+'.json'),{}) for game in args.games}
    backups = {game:{p:p.read_bytes() if p.exists() else None for p in transaction_files(game)} for game in args.games}
    try:
        http = sources.CachedHTTP(WORK/'http-cache',args.offline)
        news, issues, report['sources'] = news_discovery(http,registry,args.games,full)
        for game in args.games:
            if not any(provider['game']==game for provider in registry['news']):
                issues.append({'game':game,'reason':'official_news_provider_missing','sourceUrl':before[game].get('source',{}).get('url',''),
                    'detail':'Catalogue collector is configured; independent official-news discovery still needs a verified provider.',
                    'requiresModel':False})
        for game in args.games:
            status = {'status':'news_only' if args.news_only else 'success','steps':[]}
            if not args.news_only:
                for job in registry['jobs'][game]:
                    command = [sys.executable,'-X','utf8',str(ROOT/'scripts/weapon_weekly_child.py'),*collector_args(job,args.offline,full,registry['overlapDays'])]
                    step = execute(command,game+'-'+job[0],folder)
                    status['steps'].append(step)
                    if step['exitCode']:
                        rollback(backups[game]);status['status']='failed_previous_snapshot_preserved'
                        issues.append({'game':game,'reason':'collector_failed','sourceUrl':before[game].get('source',{}).get('url',''),
                            'detail':job[0]+' exit '+str(step['exitCode']), 'requiresModel':False})
                        break
            current = sources.read(DATA/(game+'.json'))
            merged = sources.merge_news(before[game].get('sourceUpdates',[]),current.get('sourceUpdates',[]),news[game])
            accepted, image_issues = sources.verify_news_images(http,merged)
            issues.extend({**issue,'game':game} for issue in image_issues)
            merged = sources.merge_news(before[game].get('sourceUpdates',[]),accepted)
            if merged:
                current['sourceUpdates'] = merged
            else:
                current.pop('sourceUpdates',None)
            snapshot_path = DATA/(game+'.json')
            original_bytes = backups[game][snapshot_path] or b''
            sources.atomic(snapshot_path,current,compact=original_bytes.count(b'\n')<5)
            status.update(changes(before[game],current))
            report['games'][game]=status
            print(game+': '+status['status']+'; added='+str(status['added'])+'; updated='+str(status['modified'])+'; newsChanged='+str(status['newsChanged']),flush=True)
        if 'delta-force' in args.games:
            issues.extend(unmapped_delta())
        queue, batch = sources.reconcile_queue(sources.read(WORK/'review-queue.json',[]),issues,report['runId'],registry['maxModelItems'])
        recovered = {source['sourceUrl'] for source in report['sources'] if source['status']=='success'}
        for issue in queue:
            if issue['reason']=='discovery_failed' and issue['sourceUrl'] in recovered:
                issue.update(status='resolved',resolvedAt=sources.stamp())
        sources.atomic(WORK/'review-queue.json',queue)
        sources.atomic(WORK/'model-batch.json',batch)
        report.update(http=http.counts,newModelItems=len(batch),newIssues=len(issues),
            partial=any(source['status'] in {'failed','partial','changed_needs_review'} for source in report['sources']) or
                any('failed' in g['status'] for g in report['games'].values()) or
                any(issue['reason'] in {'official_news_provider_missing','news_image_failed'} for issue in issues))
        commands = [[sys.executable,'scripts/build_weapon_manifest.py'],['node','scripts/validate_weapon_skins.cjs'],
            [sys.executable,'scripts/test_weapon_delta_bilibili.py']]
        if not args.skip_build:
            commands.append([sys.executable,'scripts/build_public_site.py'])
        for index,command in enumerate(commands):
            step = execute(command,'check-'+str(index),folder)
            report['checks'].append(step)
            if step['exitCode']:
                raise RuntimeError('Validation or build failed; publication stopped: '+command[-1])
        if args.publish:
            report['publication'] = publish()
            report['publication']['onlineVerification'] = verify_publication(args.games)
        report['status'] = 'offline_verified' if args.offline else 'partial' if report['partial'] else 'success'
        return 3 if report['status']=='partial' else 0
    except Exception as error:
        report.update(status='failed',error=str(error))
        print('Weekly pipeline failed: '+str(error),flush=True)
        return 1
    finally:
        report['finishedAt']=sources.stamp()
        sources.atomic(folder/'report.json',report)
        sources.atomic(WORK/'last-run.json',report)
        lock.unlink(missing_ok=True)
        print(json.dumps({'status':report['status'],'report':'local-only/weapon-skins/weekly/last-run.json',
            'modelItems':report.get('newModelItems',0),'llmCalls':0},ensure_ascii=False),flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
