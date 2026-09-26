"""Post due items from schedule.json to the Red Brick Lettings Facebook Page and Instagram.

Run every 30 minutes by .github/workflows/post.yml. Standard library only.

schedule.json  [{"id": "2026-10-01-fb-V01", "at": "2026-10-01 12:00", "network": "facebook" | "instagram",
                 "image": "2026-10/images/V01.jpg", "caption": "..."}]   ("at" is London time)
posted.json    {"<id>": {"posted_at": "...", "result": "<post id>"}}  written back to the repo by the workflow

  python poster/post.py            post everything due now
  python poster/post.py --test     check tokens and permissions: uploads the test image to Facebook UNPUBLISHED and
                                   deletes it, creates an Instagram container WITHOUT publishing it
  python poster/post.py --selftest check the scheduling logic, no network
  python poster/post.py --schedule-facebook
                                   hand every future Facebook row to Facebook's own scheduler now (shows in Meta
                                   Business Suite); rows Facebook refuses stay for the timer to post on the day
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

G = 'https://graph.facebook.com/v26.0/'
RAW = 'https://raw.githubusercontent.com/adminredbrick-dotcom/redbrick-social/main/'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONDON = ZoneInfo('Europe/London')
# ponytail: a post more than 12 hours late is skipped rather than flooded out after an outage. GitHub runs the
# 30-minute timer only every 3-5 hours on this repo (seen 25-26/09/2026), so 12h covers that with room to spare.
LATE = dt.timedelta(hours=12)


def call(method, path, **params):
    body = urllib.parse.urlencode(params)
    url = G + path + ('' if method == 'POST' else '?' + body)
    req = urllib.request.Request(url, data=body.encode() if method == 'POST' else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = json.load(e).get('error', {})
        raise RuntimeError('%s (code %s/%s)' % (err.get('message'), err.get('code'), err.get('error_subcode'))) from None


def when(p):
    return dt.datetime.strptime(p['at'], '%Y-%m-%d %H:%M').replace(tzinfo=LONDON)


def due(schedule, posted, now):
    return [p for p in schedule if p['id'] not in posted and when(p) <= now < when(p) + LATE]


def stamp(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(dt.datetime.fromisoformat(str(v).replace('+0000', '+00:00')).timestamp())


def schedule_facebook(schedule, posted, env, now):
    tok = env['FB_PAGE_TOKEN']
    # only posts still waiting in Facebook's scheduler can be moved; anything published is never touched
    # keyed on the object number: Facebook lists "<page>_<number>", we stored the bare number
    waiting = {x['id'].split('_')[-1]: (x['id'], stamp(x.get('scheduled_publish_time'))) for x in call(
        'GET', env['FB_PAGE_ID'] + '/scheduled_posts', fields='id,scheduled_publish_time', limit='100',
        access_token=tok).get('data', [])}
    for p in schedule:
        if p['network'] != 'facebook':
            continue
        old = posted.get(p['id'], {}).get('result', '')
        if old.startswith('fb-scheduled:'):                  # already with Facebook: move it only if the date changed
            num = old.split(':', 1)[1].split('_')[-1]
            if num not in waiting or waiting[num][1] == int(when(p).timestamp()):
                continue
            try:
                call('DELETE', waiting[num][0], access_token=tok)
            except Exception as e:                           # leave it alone rather than risk posting twice
                print('could not move', p['id'], '- left in its old slot -', e)
                continue
            del posted[p['id']]
            print('removed old slot', p['id'])
        elif p['id'] in posted:
            continue
        if when(p) < now + dt.timedelta(minutes=20):
            continue
        try:
            r = call('POST', env['FB_PAGE_ID'] + '/photos', url=RAW + p['image'], caption=p['caption'], published='false',
                     scheduled_publish_time=str(int(when(p).timestamp())), unpublished_content_type='SCHEDULED',
                     access_token=env['FB_PAGE_TOKEN'])
            posted[p['id']] = {'posted_at': 'scheduled on Facebook ' + now.isoformat(timespec='minutes'),
                               'result': 'fb-scheduled:' + (r.get('post_id') or r['id'])}
            print('scheduled on Facebook', p['id'], p['at'])
        except Exception as e:
            print('left for the timer', p['id'], p['at'], '-', e)


def post_facebook(p, page, token, test=False):
    r = call('POST', page + '/photos', url=RAW + p['image'], caption=p['caption'],
             published='false' if test else 'true', access_token=token)
    if test:
        call('DELETE', r['id'], access_token=token)
    return r.get('post_id') or r['id']


def post_instagram(p, ig, token, test=False):
    if not p['image'].lower().endswith(('.jpg', '.jpeg')):
        raise RuntimeError('Instagram only accepts JPEG: ' + p['image'])
    container = call('POST', ig + '/media', image_url=RAW + p['image'], caption=p['caption'], access_token=token)['id']
    for _ in range(20):
        status = call('GET', container, fields='status_code', access_token=token)['status_code']
        if status == 'FINISHED':
            break
        if status in ('ERROR', 'EXPIRED'):
            raise RuntimeError('Instagram rejected the image: ' + status)
        time.sleep(3)
    else:
        raise RuntimeError('Instagram was still processing the image after 60 seconds')
    if test:
        return container
    return call('POST', ig + '/media_publish', creation_id=container, access_token=token)['id']


def send(p, env, test=False):
    if p['network'] == 'facebook':
        return post_facebook(p, env['FB_PAGE_ID'], env['FB_PAGE_TOKEN'], test)
    if p['network'] == 'instagram':
        return post_instagram(p, env['IG_USER_ID'], env.get('IG_PAGE_TOKEN') or env['FB_PAGE_TOKEN'], test)
    raise RuntimeError('unknown network: ' + p['network'])


def selftest():
    now = dt.datetime(2026, 10, 1, 12, 10, tzinfo=LONDON)
    s = [{'id': 'due', 'at': '2026-10-01 12:00'}, {'id': 'future', 'at': '2026-10-01 13:00'},
         {'id': 'too-late', 'at': '2026-09-30 23:00'}, {'id': 'done', 'at': '2026-10-01 11:00'}]
    assert [p['id'] for p in due(s, {'done': {}}, now)] == ['due']
    summer = dt.datetime(2026, 7, 1, 11, 0, tzinfo=dt.timezone.utc)          # 12:00 London in BST
    assert [p['id'] for p in due([{'id': 'bst', 'at': '2026-07-01 12:00'}], {}, summer)] == ['bst']
    assert stamp(1790852400) == stamp('1790852400') == stamp('2026-10-01T11:00:00+0000') == 1790852400
    assert int(when({'at': '2026-10-01 12:00'}).timestamp()) == 1790852400        # 12:00 London in BST
    print('selftest ok')


def main():
    if '--selftest' in sys.argv:
        return selftest()
    env = os.environ
    if '--test' in sys.argv:
        checks = [{'network': 'facebook', 'image': 'test/V01_feed.png', 'caption': 'Connection test - not published'}]
        if env.get('IG_USER_ID'):
            checks.append({'network': 'instagram', 'image': 'test/V01_feed.jpg', 'caption': 'Connection test - not published'})
        else:
            print('Instagram: IG_USER_ID not set, skipped')
        bad = 0
        for c in checks:
            try:
                print('%s: OK (%s, nothing published)' % (c['network'], send(c, env, test=True)))
            except Exception as e:
                bad += 1
                print('%s: FAILED - %s' % (c['network'], e))
        try:                                  # what Facebook itself is holding in its scheduler
            sp = call('GET', env['FB_PAGE_ID'] + '/scheduled_posts', fields='id,scheduled_publish_time,message,is_published',
                      limit='100', access_token=env['FB_PAGE_TOKEN']).get('data', [])
            print('Facebook scheduler holds %d post(s):' % len(sp))
            for x in sp:
                print('  ', x.get('scheduled_publish_time'), x['id'], (x.get('message') or '').split('\n')[0][:60])
        except Exception as e:
            print('Facebook scheduler: could not list - %s' % e)
        if '--recent' in sys.argv:            # read-only: what is already published, to reuse or pin
            for x in call('GET', env['FB_PAGE_ID'] + '/published_posts', limit='25', access_token=env['FB_PAGE_TOKEN'],
                          fields='id,created_time,message,permalink_url,full_picture,is_pinned').get('data', []):
                print('FBPOST', json.dumps(x, ensure_ascii=False))
            if env.get('IG_USER_ID'):
                for x in call('GET', env['IG_USER_ID'] + '/media', limit='25',
                              access_token=env.get('IG_PAGE_TOKEN') or env['FB_PAGE_TOKEN'],
                              fields='id,timestamp,caption,permalink,media_type,media_url').get('data', []):
                    print('IGPOST', json.dumps(x, ensure_ascii=False))
        sys.exit(1 if bad else 0)

    schedule = json.load(open(os.path.join(ROOT, 'schedule.json'), encoding='utf-8'))
    posted_path = os.path.join(ROOT, 'posted.json')
    posted = json.load(open(posted_path, encoding='utf-8'))
    now = dt.datetime.now(LONDON)
    failed = 0
    # every run hands Facebook posts that are now inside its 30-day window to Facebook's own scheduler, so Facebook
    # never waits on this timer; a failure here must not stop the posting below
    try:
        schedule_facebook(schedule, posted, env, now)
    except Exception as e:
        print('Facebook scheduling check failed -', e)
    for p in due(schedule, posted, now):
        try:
            posted[p['id']] = {'posted_at': now.isoformat(timespec='minutes'), 'result': send(p, env)}
            print('posted', p['id'])
        except Exception as e:                  # not recorded, so the next run retries until the 6-hour window closes
            failed += 1
            print('FAILED', p['id'], '-', e)
    with open(posted_path, 'w', encoding='utf-8') as f:
        json.dump(posted, f, indent=1, ensure_ascii=False)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
