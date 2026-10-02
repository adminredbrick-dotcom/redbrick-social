"""One-time Threads connector for the Red Brick Social Poster app. Run it by double-clicking CONNECT THREADS.bat.

Before running: the app has the "Access the Threads API" use case (threads_basic + threads_content_publish), the
redirect URL below is in its Threads settings, and @red_brick_lettings has accepted the Threads Tester invite.

It reads the Threads app ID, the Threads app secret and the one-time code straight from the clipboard, opens the
Threads approval window in the browser, turns the code into a 60-day token and saves it into the GitHub repository's
secrets with the gh tool. Optionally it also saves a GitHub token (SECRETS_PAT) so the weekly refresh-threads job can
keep renewing the Threads token. Tokens are never printed and never written to disk. A plain summary with no tokens
goes to connect_threads_result.txt so Claude can read what happened.
"""
import json
import os
import re
import subprocess
import tkinter
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

REPO = 'adminredbrick-dotcom/redbrick-social'
REDIRECT = 'https://adminredbrick-dotcom.github.io/redbrick-social/connected.html'
SCOPES = 'threads_basic,threads_content_publish'
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'connect_threads_result.txt')
lines = []


def say(t=''):
    print(t)
    lines.append(t)


def finish(code=0):
    with open(OUT, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print('\nSummary saved to connect_threads_result.txt (no tokens in it). You can close this window.')
    raise SystemExit(code)


def request(method, url, **params):
    body = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + ('' if method == 'POST' else '?' + body),
                                 data=body.encode() if method == 'POST' else None, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            err = json.load(e).get('error', {})
        except ValueError:
            err = {}
        msg = err.get('message') if isinstance(err, dict) else err
        say('Threads refused %s: %s' % (url.split('?')[0], msg or e.reason))
        finish(1)


def gh(kind, name, value):
    cmd = ['gh', kind, 'set', name, '--repo', REPO] + (['--body', value] if kind == 'variable' else [])
    r = subprocess.run(cmd, input=None if kind == 'variable' else value, text=True, capture_output=True)
    if r.returncode:
        say('Could not save %s to GitHub: %s' % (name, r.stderr.strip()))
        finish(1)
    say('Saved %s %s to GitHub' % (kind, name))


def from_clipboard(what, pattern, hint, optional=False):
    while True:
        typed = input('   Copy it, then press Enter here (no need to paste)%s: ' % (' - or type S and Enter to skip' if optional else ''))
        if optional and typed.strip().lower() == 's':
            return ''
        tk = tkinter.Tk()
        tk.withdraw()
        try:
            value = tk.clipboard_get().strip()
        except tkinter.TclError:
            value = ''
        tk.destroy()
        value = re.sub(r'#_$', '', value)
        if re.fullmatch(pattern, value):
            print('   Got the %s (%d characters). Not shown.' % (what, len(value)))
            return value
        print('   That is not the %s: the clipboard holds %d characters. %s Try again.' % (what, len(value), hint))


print('RED BRICK - connect Threads to the posting app\n')
print('1. developers.facebook.com > Red Brick Social Poster > Use cases > Threads > Settings (or App settings > Basic')
print('   while the Threads use case is selected). Copy the THREADS app ID (not the Facebook one).')
app_id = from_clipboard('Threads app ID', r'\d{10,20}', 'It is a long number with no letters.')
print('\n2. On the same page, show the THREADS app secret and copy it.')
secret = from_clipboard('Threads app secret', r'[0-9a-f]{32}', 'The app secret is exactly 32 characters, 0-9 and a-f.')

auth = 'https://threads.com/oauth/authorize?' + urllib.parse.urlencode(
    {'client_id': app_id, 'redirect_uri': REDIRECT, 'scope': SCOPES, 'response_type': 'code', 'state': 'redbrick'})
print('\n3. Your browser is opening the Threads approval window. Log in as red_brick_lettings and allow it.')
print('   You land on a Red Brick page - click "Copy the code".')
webbrowser.open(auth)
code = from_clipboard('one-time code', r'[A-Za-z0-9_\-]{20,}', 'Click "Copy the code" on the Red Brick page first.')
say('')

short = request('POST', 'https://graph.threads.com/oauth/access_token', client_id=app_id, client_secret=secret,
                code=code, grant_type='authorization_code', redirect_uri=REDIRECT)
say('Approval accepted.')
long_ = request('GET', 'https://graph.threads.net/access_token', grant_type='th_exchange_token',
                client_secret=secret, access_token=short['access_token'])
say('Token extended: valid for %d days (renewed every Monday by the refresh-threads job).'
    % (int(long_.get('expires_in', 0)) // 86400))
me = request('GET', 'https://graph.threads.net/v1.0/me', fields='id,username', access_token=long_['access_token'])
say('Threads account: @%s (id %s)' % (me.get('username'), me['id']))
if me.get('username') != 'red_brick_lettings':
    say('WARNING: that is not @red_brick_lettings - check you logged in to the right Threads account.')
gh('secret', 'THREADS_TOKEN', long_['access_token'])
gh('variable', 'THREADS_USER_ID', me['id'])

print('\n4. Automatic renewal (recommended). On github.com, signed in as adminredbrick-dotcom:')
print('   Settings > Developer settings > Fine-grained tokens > Generate new token.')
print('   Name "redbrick renew", expiry 1 year, Repository access: only redbrick-social,')
print('   Permissions > Repository > Secrets: Read and write. Generate, then copy the token.')
pat = from_clipboard('GitHub token', r'github_pat_[A-Za-z0-9_]{60,}', 'It starts with github_pat_.', optional=True)
if pat:
    gh('secret', 'SECRETS_PAT', pat)
    say('Automatic renewal is on. The GitHub token itself expires in a year - renew it then.')
else:
    say('Skipped the GitHub token: the Threads token will stop working in about 60 days unless this is run again.')
say('\nALL DONE. Tell Claude "threads connected".')
finish(0)
