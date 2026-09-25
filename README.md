# Red Brick Lettings - social media

Public home for the post graphics and each month's posting schedule for Red Brick Lettings, Peterborough.

- `YYYY-MM/images/` - the approved graphics for that month (PNG)
- `YYYY-MM/schedule.json` - date, time, platform and caption for every post
- `test/` - throwaway files used to check that platforms can fetch images from here
- `poster/post.py` - posts whatever is due in `schedule.json` to the Facebook Page and Instagram; run every 30 minutes by `.github/workflows/post.yml`, which logs what went out in `posted.json`
- `CONNECT META.bat` - one-time connector that turns the Meta token into a never-expiring Page token and saves it into this repository's secrets (never shown or stored on disk)
- `privacy.html` - privacy policy for the Meta app

Only graphics and captions live here. No tenant, landlord or business records.
