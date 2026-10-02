"""Post due items from schedule.json to the Red Brick Lettings Facebook Page, Instagram and Threads.

Run by .github/workflows/post.yml. Standard library only.

schedule.json  [{"id": "2026-10-01-fb-V01", "at": "2026-10-01 12:00", "network": "facebook" | "instagram" | "threads",
                 "image": "2026-10/images/V01.jpg", "caption": "..."}]   ("at" is London time)
posted.json    {"<id>": {"posted_at": "...", "result": "<post id>"}}  written back to the repo by the workflow

  python poster/post.py            post everything due now
  python poster/post.py --wait     also stay running and post each item due in the next few hours ON the minute
                                   (Instagram and Threads have no scheduler of their own, and GitHub's timer only
                                   starts this every 3-5 hours)
  python poster/post.py --test     check tokens and permissions: uploads the test image to Facebook UNPUBLISHED and
                                   deletes it, creates Instagram and Threads containers WITHOUT publishing them
  python poster/post.py --selftest check the scheduling logic, no network
  python poster/post.py --schedule-facebook
                                   hand every future Facebook row to Facebook's own scheduler now (shows in Meta
                                   Business Suite); rows Facebook refuses stay for the timer to post on the day
  python poster/post.py --refresh-threads FILE
                                   renew the 60-day Threads token and write the new one to FILE (never printed)
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
T = 'https://graph.threads.net/v1.0/'
RAW = 'https://raw.githubusercontent.com/adminredbrick-dotcom/redbrick-social/main/'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LONDON = ZoneInfo('Europe/London')
# ponytail: a post more than 12 hours late is skipped rather than flooded out after an outage. GitHub runs the
# 30-minute timer only every 3-5 hours on this repo (seen 25-26/09/2026), so 12h covers that with room to spare.
LATE = dt.timedelta(hours=12)
# a --wait run stays up for posts due in the next 5h15m; GitHub stops a job at 6h, and its timer starts a new run
# every 3-5 hours, so the windows overlap. Anything a gap misses is still caught by LATE above.
HORIZON = dt.timedelta(minutes=315)


def call(method, path, base=G, **params):
    body = urllib.parse.urlencode(params)
    url = base + path + ('' if method == 'POST' else '?' + body)
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


def upcoming(schedule, posted, now):
    """Items not yet posted (or handed to Facebook's scheduler) that fall due within the next HORIZON, soonest first."""
    return sorted((p for p in schedule if p['id'] not in posted and now < when(p) <= now + HORIZON), key=when)


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


def post_threads(p, user, token, test=False):
    if len(p['caption'].encode('utf-8')) > 500:          # Threads counts emojis by their UTF-8 bytes
        raise RuntimeError('Threads allows 500 characters; this caption is %d bytes' % len(p['caption'].encode('utf-8')))
    container = call('POST', user + '/threads', base=T, media_type='IMAGE', image_url=RAW + p['image'],
                     text=p['caption'], access_token=token)['id']
    time.sleep(30)                                         # Meta recommends about 30 seconds before publishing
    for _ in range(12):
        st = call('GET', container, base=T, fields='status,error_message', access_token=token)
        if st.get('status') == 'FINISHED':
            break
        if st.get('status') in ('ERROR', 'EXPIRED'):
            raise RuntimeError('Threads rejected the image: %s' % (st.get('error_message') or st['status']))
        time.sleep(5)
    else:
        raise RuntimeError('Threads was still processing the image after 90 seconds')
    if test:
        return container
    return call('POST', user + '/threads_publish', base=T, creation_id=container, access_token=token)['id']


def refresh_threads(env, out):
    """Renew the long-lived Threads token (valid 60 days, renewable once it is a day old) and write it to `out`."""
    r = call('GET', 'refresh_access_token', base='https://graph.threads.net/', grant_type='th_refresh_token',
             access_token=env['THREADS_TOKEN'])
    with open(out, 'w', encoding='utf-8') as f:
        f.write(r['access_token'])
    print('Threads token renewed, valid for another %d days' % (int(r.get('expires_in', 0)) // 86400))


def send(p, env, test=False):
    if p['network'] == 'facebook':
        return post_facebook(p, env['FB_PAGE_ID'], env['FB_PAGE_TOKEN'], test)
    if p['network'] == 'instagram':
        return post_instagram(p, env['IG_USER_ID'], env.get('IG_PAGE_TOKEN') or env['FB_PAGE_TOKEN'], test)
    if p['network'] == 'threads':
        return post_threads(p, env.get('THREADS_USER_ID') or 'me', env['THREADS_TOKEN'], test)
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
    u = [{'id': 'later', 'at': '2026-10-01 18:00'}, {'id': 'soon', 'at': '2026-10-01 12:30'},
         {'id': 'past', 'at': '2026-10-01 12:00'}, {'id': 'beyond', 'at': '2026-10-01 17:30'},
         {'id': 'with-facebook', 'at': '2026-10-01 13:00'}]
    # at 12:10 the 5h15m window ends 17:25: 'past' belongs to due(), 'beyond' to the next run, handed-over ones skipped
    assert [p['id'] for p in upcoming(u, {'with-facebook': {}}, now)] == ['soon']
    assert [p['id'] for p in upcoming(u, {}, now.replace(hour=13))] == ['beyond', 'later']
    print('selftest ok')


def main():
    if '--selftest' in sys.argv:
        return selftest()
    env = os.environ
    if '--refresh-threads' in sys.argv:
        return refresh_threads(env, sys.argv[sys.argv.index('--refresh-threads') + 1])
    if '--test' in sys.argv:
        checks = [{'network': 'facebook', 'image': 'test/V01_feed.png', 'caption': 'Connection test - not published'}]
        if env.get('IG_USER_ID'):
            checks.append({'network': 'instagram', 'image': 'test/V01_feed.jpg', 'caption': 'Connection test - not published'})
        else:
            print('Instagram: IG_USER_ID not set, skipped')
        if env.get('THREADS_TOKEN'):
            checks.append({'network': 'threads', 'image': 'test/V01_feed.jpg', 'caption': 'Connection test - not published'})
        else:
            print('Threads: THREADS_TOKEN not set, skipped')
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
                          fields='id,created_time,message,permalink_url,full_picture').get('data', []):
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
    def save():                                 # after every post, so a run stopped mid-wait never posts twice
        with open(posted_path, 'w', encoding='utf-8') as f:
            json.dump(posted, f, indent=1, ensure_ascii=False)

    def publish(p):
        nonlocal failed
        try:
            posted[p['id']] = {'posted_at': dt.datetime.now(LONDON).isoformat(timespec='minutes'), 'result': send(p, env)}
            print('posted', p['id'], p['at'])
        except Exception as e:                  # not recorded, so a later run retries until the 12-hour window closes
            failed += 1
            print('FAILED', p['id'], '-', e)
        save()

    for p in due(schedule, posted, now):
        publish(p)
    save()
    if '--wait' in sys.argv:
        for p in upcoming(schedule, posted, now):
            wait = (when(p) - dt.datetime.now(LONDON)).total_seconds()
            print('waiting %d min for %s at %s' % (max(wait, 0) // 60, p['id'], p['at']), flush=True)
            time.sleep(max(wait, 0))
            if p['id'] not in posted:           # Facebook's scheduler may have taken it meanwhile
                publish(p)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
