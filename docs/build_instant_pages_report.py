"""The report of the instant-pages work: one self-contained HTML page (every image and the video inline), built from the files in its folder.

    python3 docs/build_instant_pages_report.py /home/bam/io-diagnostics/reports/media/instant-pages-2026-11-04

Reads hero.mp4, hero.gif, hero-facts.json (docs/shoot_instant_pages.js) and the preview-*.png stills; writes report.html.
"""

import base64
import json
import os
import sys

import cv2

FOLDER = sys.argv[1]
SHA = 'fc01258'


def data_uri(name: str, mime: str) -> str:
    with open(os.path.join(FOLDER, name), 'rb') as handle:
        return f'data:{mime};base64,{base64.b64encode(handle.read()).decode()}'


def still(name: str, caption: str) -> str:
    picture = cv2.imread(os.path.join(FOLDER, name))
    if picture is None:
        raise SystemExit(f'no image {name}')
    picture = cv2.resize(picture, (900, round(picture.shape[0] * 900 / picture.shape[1])), interpolation=cv2.INTER_AREA)
    ok, data = cv2.imencode('.jpg', picture, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if not ok:
        raise SystemExit(f'could not encode {name}')
    return f'<figure><img alt="{caption}" src="data:image/jpeg;base64,{base64.b64encode(data.tobytes()).decode()}"><figcaption>{caption}</figcaption></figure>'


def rows(lines) -> str:
    return ''.join('<tr>' + ''.join(f'<td>{cell}</td>' for cell in line) + '</tr>' for line in lines)


def items(lines) -> str:
    return '<ul>' + ''.join(f'<li>{line}</li>' for line in lines) + '</ul>'


with open(os.path.join(FOLDER, 'hero-facts.json'), encoding='utf-8') as handle:
    hero = json.load(handle)
times = [paint['ms'] for paint in hero['paints']]

MEASURES = [
    ('<code>/node</code> on Preview, adapter', '22 s', '1.9–2.3 ms'),
    ('<code>/node</code> on Preview, computer', '3.8 s', '1.4 ms'),
    ('<code>/node</code> median / p95 under load (fixture)', '852 / 2,021 ms', '3.7 / 9.2 ms'),
    ('<code>/state</code>, <code>/activity</code>, <code>/checks</code> on Preview', '0.31, 0.26, 0.57 s', '1.5, 1.6, 3–14 ms'),
    ('Preview server CPU, nobody connected', '88%', '11–14%'),
    ('Preview server CPU, one page open', '93%', '15–21%'),
    ('First useful paint with <code>/node</code> 2.5 s away', 'nothing in 20 s', '6–13 ms'),
    ('Preview restart with a page open', 'not recorded', '0.42–0.49 s gone; 0 red and 0 blank in 814 samples'),
]

CAUSES = [
    'The page asked again on every tick and only counted the newest answer, so at 3 s an answer nothing ever counted: 645 requests in 30 s and nothing drawn.',
    'That queue never emptied, which was the 88% CPU.',
    '<code>/node</code> serialized every matching event, then kept 20.',
    'Each hardware-holder check walked <code>/proc</code> twice, 0.54 s a walk.',
    'The log fold was quadratic, and every read built its own view under the host lock.',
    'The server came up last, so a restart lost the port for seconds.',
    'Nothing on the page had a timeout, and "no events yet" was drawn before the first answer.',
    '"checking…" dropped the last answer, and history lived only in memory.',
]

DONE = [
    f'Live Preview, walked just now at <code>{SHA}</code>: six pages drawn in {min(times)}–{max(times)} ms from click, Playwright\'s click included, machine under load; 0 page errors',
    '<code>test_instant_pages.js</code> 50/50 and <code>test_not_run_page.js</code> 14/14, rerun on main by the dispatcher',
    'pytest 1457 passed at <code>1519fb7</code>; <code>fc01258</code> on top of it changes only the PEP',
    'Page suites at <code>1519fb7</code>: entity_page 92/92, utilities 90/90, utilities_ux 25/25, activity_view 78/78, layouts 200/200, threed_view 68/68',
    'Run-end fix: 7 of 7 mutations went red',
    'Edits to existing tests read by the dispatcher: no assertion changed. Two suites now hold the feed unanswered, one takes the page\'s own timestamp, one prints the state on failure',
]

DECIDED = [
    'Reads answer from a published view (PEP 0003). Undo: <code>git -C /home/bam/io-diagnostics revert 9c5147b</code>, with the fixes after it',
    'A run\'s end reaches clients as one moment; the host and disk still get it at once. Undo: <code>git -C /home/bam/io-diagnostics revert 066c8f1</code>',
    'Once there is a feed, its snapshot is the page\'s base. Undo: <code>git -C /home/bam/io-diagnostics revert 4154682</code>',
    'An entity log\'s band no longer covers the newest row. Undo: <code>git -C /home/bam/io-diagnostics revert 84f5dde</code>',
]

LEFT = [
    'Next: the OS skeleton read every 2 s is most of the remaining 11–14% CPU',
    'Next: view builds still hold <code>self.lock</code>',
    'Next: prototypes d0–d5 still draw "loading…"',
    'Open tabs need a reload to get the new page code',
    'Next: <code>web/dx/</code> should read <code>check.not_run</code> (Data-UI Agent)',
]

UNSETTLED = [
    '<code>test_runs_r5</code> reload check failed once in 11 rounds at load around 45. Cause unknown; the guess is <code>y</code> and Enter arriving out of order. The check now prints the state, so the next failure can be read',
    '<code>test_dx_add_at_moment</code> was 2 of 5 here against 0 of 4 before at high load, and 6 of 6 on both at load 10 or below. A small sample',
    '<code>test_dx_page</code> fails 2 of 4 on this tree and the old one. <code>test_diag_variants</code> "d1b order" and the <code>test_roster_page</code> crash are unchanged',
    'The old red chip\'s cause is reasoned from the code, not watched. The cold-start race was forced on a fixture. Tray mode was not run',
    '<code>bench_classic</code> misses one target by 4 ms (54 vs 50). Six alternating runs say noise: main\'s worst was 209 ms, the lane\'s 111 ms',
]

PAGE = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RobotDx Instant Pages</title><style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#656d76;--line:#d8dee4;--soft:#f6f8fa}}
@media (prefers-color-scheme:dark){{:root{{--bg:#16181c;--fg:#e6e8eb;--muted:#9aa2ab;--line:#30363d;--soft:#1f2329}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:24px 16px 64px}}h1{{font-size:24px;margin:0 0 6px}}h2{{font-size:17px;margin:32px 0 8px}}
.verdict{{color:var(--muted);margin:0 0 18px}}video,img{{width:100%;display:block;border:1px solid var(--line);border-radius:6px}}
table{{border-collapse:collapse;width:100%}}td,th{{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{color:var(--muted);font-weight:500}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}@media (max-width:700px){{.pair{{grid-template-columns:1fr}}}}
figure{{margin:0}}figcaption{{color:var(--muted);font-size:13px;margin-top:4px}}code{{background:var(--soft);padding:1px 4px;border-radius:3px;font-size:13px}}li{{margin:4px 0}}
</style></head><body><main>
<h1>RobotDx pages no longer sit on "loading…"</h1>
<p class="verdict">On <code>main</code> @ <code>{SHA}</code>, live on Preview, not on Stable. Four small issues below are unexplained or unchanged, none of them a loading problem.</p>
<video controls autoplay muted loop playsinline poster=""><source src="{data_uri('hero.mp4', 'video/mp4')}" type="video/mp4"><img alt="Clicking through RobotDx pages on Preview" src="{data_uri('hero.gif', 'image/gif')}"></video>
<p class="verdict">Preview on the real host, Nov 4 at 23:50. Clicks only move between rows and tabs; nothing writes. The rack was in a session, so its boards read unknown, which is correct.</p>
<h2>Before and after</h2><table><tr><th>Measure</th><th>Before</th><th>After</th></tr>{rows(MEASURES)}</table>
<h2>The same page</h2><div class="pair">
{still('preview-before-computer.png', 'Before: the computer, "loading…" and nothing else')}{still('preview-after-computer.png', 'After: the same page, full at first paint')}
{still('preview-before-adapter.png', 'Before: an adapter')}{still('preview-after-adapter.png', 'After: an adapter')}
</div>
<h2>Log tab</h2><div class="pair">
{still('preview-log-tab-newest-row-cut-9c5147b.png', 'Before: the date band hid the newest row, since 0ad96f2')}{still('preview-after-log-tab.png', 'After: the newest row is whole')}
</div>
<h2>A restart with a page open</h2><div class="pair">{still('preview-restart-after.png', 'After a real Preview restart: selection, boards and health bars all held')}</div>
<h2>Why it hung</h2><ol>{''.join(f'<li>{line}</li>' for line in CAUSES)}</ol>
<h2>Three races from the first landing</h2>
<p>These each lost about one round in ten, so one green run missed them; repeated suites caught them. All are fixed and recorded as rules in PEP 0003.</p>
{items(["A finished run could look like it was still running.", "A tab opened during a cold start could lose boards from the tree.", "A press on Run could silently do nothing. The bug was older; the page now says \"Asking…\" until it has the token."])}
<h2>Done and proved</h2>{items(DONE)}
<h2>Decided for you</h2>{items(DECIDED)}
<h2>Not settled</h2>{items(UNSETTLED)}
<h2>Left</h2>{items(LEFT)}
<h2>Deliberately not done</h2>{items(["No Make Stable: that is your button", "No audit: you didn't ask for one. Three races got past a green run, so one may be worth it"])}
</main></body></html>'''

with open(os.path.join(FOLDER, 'report.html'), 'w', encoding='utf-8') as handle:
    handle.write(PAGE)
print(os.path.getsize(os.path.join(FOLDER, 'report.html')))
