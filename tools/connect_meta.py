"""One-time Meta connector for the Red Brick Social Poster app. Run it by double-clicking CONNECT META.bat.

It asks for two things, both typed hidden: the token from Graph API Explorer and the app secret. It turns them into a
never-expiring Page token, finds the Instagram account linked to the Page, and saves everything straight into the
GitHub repository's secrets with the gh tool. Tokens are never printed and never written to disk.
A plain summary with no tokens goes to connect_result.txt so Claude can read what happened.
"""
import getpass
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request

APP_ID = '1439752518022456'           # Red Brick Social Poster (public id, shown in the dashboard address bar)
PAGE_ID = '100373344900481'           # Red Brick Lettings Facebook Page
REPO = 'adminredbrick-dotcom/redbrick-social'
G = 'https://graph.facebook.com/v26.0/'
NEEDED = {'pages_show_list', 'pages_manage_posts', 'pages_read_engagement', 'instagram_basic', 'instagram_content_publish'}
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'connect_result.txt')
lines = []


def say(t=''):
    print(t)
    lines.append(t)


def finish(code=0):
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\nSummary saved to connect_result.txt (no tokens in it). You can close this window.')
    raise SystemExit(code)


def get(path, **params):
    try:
        with urllib.request.urlopen(G + path + '?' + urllib.parse.urlencode(params), timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = json.load(e).get('error', {})
        say('Meta refused "%s": %s (code %s)' % (path.split('?')[0], err.get('message'), err.get('code')))
        finish(1)


def gh(kind, name, value):
    cmd = ['gh', kind, 'set', name, '--repo', REPO] + (['--body', value] if kind == 'variable' else [])
    r = subprocess.run(cmd, input=None if kind == 'variable' else value, text=True, capture_output=True)
    if r.returncode:
        say('Could not save %s to GitHub: %s' % (name, r.stderr.strip()))
        finish(1)
    say('Saved %s %s to GitHub' % (kind, name))


print('RED BRICK - connect Facebook and Instagram to the posting app\n')
print('1. In Graph API Explorer: app "Red Brick Social Poster", these permissions ticked:')
print('   ' + ', '.join(sorted(NEEDED)))
print('   Click Generate Access Token, allow the Red Brick Lettings Page AND the Instagram account,')
print('   then click the copy icon next to the token.')
user_token = getpass.getpass('   Paste it here (it stays hidden) and press Enter: ').strip()
print('\n2. developers.facebook.com > Red Brick Social Poster > App settings > Basic > App secret > Show, copy it.')
secret = getpass.getpass('   Paste it here (hidden) and press Enter: ').strip()
say('')

long_token = get('oauth/access_token', grant_type='fb_exchange_token', client_id=APP_ID,
                 client_secret=secret, fb_exchange_token=user_token)['access_token']
say('Token extended to a long-lived one.')

granted = {p['permission'] for p in get('me/permissions', access_token=long_token)['data'] if p['status'] == 'granted'}
say('Permissions granted: ' + ', '.join(sorted(granted)))
missing = NEEDED - granted
if missing:
    say('MISSING permissions: ' + ', '.join(sorted(missing)) + ' - add them in the Explorer and generate the token again.')

fields = 'name,id,access_token,instagram_business_account{id,username}'
pages = get('me/accounts', fields=fields, limit=100, access_token=long_token)['data']
say('Pages this login manages: ' + (', '.join('%s (%s)' % (p['name'], p['id']) for p in pages) or 'none listed'))
main = next((p for p in pages if p['id'] == PAGE_ID), None) or get(PAGE_ID, fields=fields, access_token=long_token)
if not main.get('access_token'):
    say('No Page token came back for Red Brick Lettings - the login has no posting role on that Page.')
    finish(1)

expiry = get('debug_token', input_token=main['access_token'], access_token=APP_ID + '|' + secret)['data'].get('expires_at', 0)
say('Page token for %s: %s' % (main['name'], 'never expires' if expiry == 0 else 'EXPIRES - timestamp %s' % expiry))
gh('secret', 'FB_PAGE_TOKEN', main['access_token'])
gh('variable', 'FB_PAGE_ID', PAGE_ID)

ig_page = main if main.get('instagram_business_account') else next(
    (p for p in pages if p.get('instagram_business_account')), None)
if not ig_page:
    say('Instagram: NOT FOUND on any Page this login manages.')
    say('Fix: link @red_brick_lettings to the Red Brick Lettings Page (Business Suite > Settings > Instagram accounts),')
    say('make sure instagram_basic is granted, then run this again. If your Page access comes through a business')
    say('portfolio, Meta also wants ads_read on the token.')
    finish(1)
ig = ig_page['instagram_business_account']
say('Instagram: @%s (id %s) linked to %s' % (ig.get('username'), ig['id'], ig_page['name']))
gh('variable', 'IG_USER_ID', ig['id'])
if ig_page['id'] != PAGE_ID:
    gh('secret', 'IG_PAGE_TOKEN', ig_page['access_token'])
say('\nALL DONE. Tell Claude "connected".')
finish(0)
