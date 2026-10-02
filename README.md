# Red Brick Lettings - social media

Public home for the post graphics and each month's posting schedule for Red Brick Lettings, Peterborough.

- `YYYY-MM/images/` - the approved graphics for that month (PNG)
- `YYYY-MM/schedule.json` - date, time, platform and caption for every post
- `test/` - throwaway files used to check that platforms can fetch images from here
- `poster/post.py` - posts whatever is due in `schedule.json` to the Facebook Page, Instagram and Threads; started by `.github/workflows/post.yml`, which logs what went out in `posted.json`. Facebook posts are handed to Facebook's own scheduler; Instagram and Threads have none, so each run stays up and posts them on the minute for the next 5 hours (GitHub's timer only starts a run every 3-5 hours)
- `.github/workflows/refresh-threads.yml` - every Monday renews the 60-day Threads token and stores it (needs the `SECRETS_PAT` secret)
- `CONNECT META.bat` - one-time connector that turns the Meta token into a never-expiring Page token and saves it into this repository's secrets (never shown or stored on disk)
- `CONNECT THREADS.bat` - one-time connector for Threads: opens the Threads approval window, saves the 60-day token and the renewal key into this repository's secrets
- `privacy.html`, `terms.html` - privacy policy and terms for the Meta app; `connected.html` - the page Threads returns to after approval (copies the one-time code, sends nothing)

Only graphics and captions live here. No tenant, landlord or business records.
