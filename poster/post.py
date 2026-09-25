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
# ponytail: a post more than 6 hours late is skipped rather than flooded out after an outage; widen if runs stall longer
LATE = dt.timedelta(hours=6)


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


def schedule_facebook(schedule, posted, env, now):
    for p in schedule:
        if p['network'] != 'facebook' or p['id'] in posted or when(p) < now + dt.timedelta(minutes=20):
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
         {'id': 'too-late', 'at': '2026-10-01 05:00'}, {'id': 'done', 'at': '2026-10-01 11:00'}]
    assert [p['id'] for p in due(s, {'done': {}}, now)] == ['due']
    summer = dt.datetime(2026, 7, 1, 11, 0, tzinfo=dt.timezone.utc)          # 12:00 London in BST
    assert [p['id'] for p in due([{'id': 'bst', 'at': '2026-07-01 12:00'}], {}, summer)] == ['bst']
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
        sys.exit(1 if bad else 0)

    schedule = json.load(open(os.path.join(ROOT, 'schedule.json'), encoding='utf-8'))
    posted_path = os.path.join(ROOT, 'posted.json')
    posted = json.load(open(posted_path, encoding='utf-8'))
    now = dt.datetime.now(LONDON)
    failed = 0
    if '--schedule-facebook' in sys.argv:
        schedule_facebook(schedule, posted, env, now)
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
