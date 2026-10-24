// USB versions, not link speeds (Zach, 2026-10-24: "instead of saying 480 Mb/s say 2.1 or 3.2 … realsense-viewer shows
// USB 2.1 upfront"). One small helper so every surface says the same thing:
//
//   window.IOUsb = { versionName(mbps), fromBcd(text), parseSpeed(text), label(text), describe({version, speed_mbps}) }  (relabel = label, the old name)
//
//   1.5 / 12 Mb/s -> USB 1.1      480 Mb/s -> USB 2.0      5 Gb/s -> USB 3.2 Gen 1
//   10 Gb/s -> USB 3.2 Gen 2      20 Gb/s -> USB 3.2 Gen 2x2      40 Gb/s -> USB4
//
// A device's own bcdUSB (sysfs `version`, "2.10") says what the DEVICE is ("USB 2.1"); the link says what it runs at.
// The raw speed is never lost: callers keep it in the tooltip. Loads in the page and in Node (tests/test_view_core.js).
'use strict';
(function (root) {
  const BY_SPEED = [[1.5, 'USB 1.1'], [12, 'USB 1.1'], [480, 'USB 2.0'], [5000, 'USB 3.2 Gen 1'], [10000, 'USB 3.2 Gen 2'],
    [20000, 'USB 3.2 Gen 2x2'], [40000, 'USB4']];
  const SPEED = /(\d+(?:\.\d+)?)\s*([kMG])b\/s/;

  // "480 Mb/s" -> 480 (megabit/s); null when the text is not a speed
  function parseSpeed(text) {
    const found = SPEED.exec(String(text ?? ''));
    if (!found) return null;
    return Number(found[1]) * ({ k: 0.001, M: 1, G: 1000 }[found[2]]);
  }

  // the USB generation of a link speed, or null for a speed USB does not define
  function versionName(mbps) {
    const speed = Number(mbps);
    if (!Number.isFinite(speed)) return null;
    const found = BY_SPEED.find(([each]) => Math.abs(each - speed) < 1e-6);
    return found ? found[1] : null;
  }

  // sysfs bcdUSB: "2.10" -> "USB 2.1", "3.20" -> "USB 3.2", "1.10" -> "USB 1.1", "2.00" -> "USB 2.0", "4.00" -> "USB4"
  function fromBcd(text) {
    const found = /^\s*(\d+)\.(\d+)\s*$/.exec(String(text ?? ''));
    if (!found) return null;
    const major = Number(found[1]);
    if (major >= 4) return 'USB4';
    const minor = found[2].length > 1 ? found[2].replace(/0+$/, '') || '0' : found[2];
    return `USB ${major}.${minor}`;
  }

  // THE label of a USB link in the page (Zach, 2026-10-24: "instead of saying 480 Mb/s say 2.1 or 3.2"). Every name and
  // message the page draws goes through it; the raw text stays in the tooltip, which callers fill from the API's own words.
  //   "USB bus 1 · 480 Mb/s"                  -> "USB bus 1 · USB 2.0"
  //   "1 device · 12 Mb/s"                    -> "1 device · USB 1.1"
  //   "on port 1-3 · USB full-speed · 12 Mb/s" -> "on port 1-3 · USB 1.1"   (the host's own speed names carry the number)
  //   "12 Mb/s" (a port's whole id)           -> "USB 1.1"
  // WHY a trailing speed is rewritten only after "USB ..." or "N device(s)", never after any word: a network card's
  // "10 Gb/s" is not a USB generation. Anything else is returned as it was.
  const HOST_SPEED_NAME = /USB (?:1\.1 low-speed|full-speed|2 high-speed|3\.2 Gen \d(?:x\d)?) · (\d+(?:\.\d+)?\s*[kMG]b\/s)/g;
  const LINKS_AT = /\b(links|linked|runs|running) at (\d+(?:\.\d+)?\s*[kMG]b\/s)/g; // "a USB 2 cable links at 480 Mb/s"
  const USB_PREFIX = /^(?:USB\b.*|.*\b\d+ devices?)$/;
  function label(text) {
    let value = String(text ?? '');
    value = value.replace(HOST_SPEED_NAME, (whole, speed) => versionName(parseSpeed(speed)) || whole);
    value = value.replace(LINKS_AT, (whole, verb, speed) => { const name = versionName(parseSpeed(speed)); return name ? `${verb} as ${name}` : whole; });
    const standalone = /^\s*(\d+(?:\.\d+)?\s*[kMG]b\/s)\s*$/.exec(value);
    if (standalone) return versionName(parseSpeed(standalone[1])) || value;
    const trailing = /^(.*\S)\s·\s(\d+(?:\.\d+)?\s*[kMG]b\/s)$/.exec(value);
    if (!trailing || !USB_PREFIX.test(trailing[1])) return value;
    const name = versionName(parseSpeed(trailing[2]));
    return name ? `${trailing[1]} · ${name}` : value;
  }

  // One device's words: its own bcdUSB when it has one, else the link's. `tip` keeps the raw speed. When the device is
  // newer than the link it runs on, both are said ("USB 3.2 · on a USB 2.0 link").
  function describe(usb) {
    if (!usb) return { text: '', tip: '' };
    const link = versionName(usb.speed_mbps);
    const own = fromBcd(usb.version);
    const raw = Number.isFinite(Number(usb.speed_mbps)) && usb.speed_mbps ? `${usb.speed_mbps >= 1000 ? `${usb.speed_mbps / 1000} Gb/s` : `${usb.speed_mbps} Mb/s`}` : '';
    // WHY only a newer device: a USB 2.1 device at high speed is just "USB 2.1"; a USB 3.2 device on a 480 Mb/s link is news
    const major = (name) => (name === 'USB4' ? 4 : Number((/(\d+)/.exec(name || '') || [0, 0])[1]));
    const text = own && link && major(own) > major(link) ? `${own} · on a ${link} link` : (own || link || '');
    return { text, tip: [own && `device ${own} (bcdUSB ${String(usb.version).trim()})`, link && `link ${link}${raw ? ` · ${raw}` : ''}`].filter(Boolean).join(' · ') };
  }

  const IOUsb = { versionName, fromBcd, parseSpeed, label, relabel: label, describe };
  root.IOUsb = IOUsb;
  if (typeof module !== 'undefined' && module.exports) module.exports = IOUsb;
})(typeof window !== 'undefined' ? window : globalThis);
