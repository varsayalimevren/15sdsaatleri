"""İETT 15SD - Bayraktar Bulvarı kalkış saatleri değişince ntfy ile bildirim gönderir."""
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = "https://iett.istanbul/RouteDetail?hkod=15SD"
SECTION = "BAYRAKTAR BULVARI KALKIŞ"
DAYS = ["İş Günleri", "Cumartesi", "Pazar"]
STATE = Path(__file__).parent / "state.json"
TOPIC = os.environ.get("NTFY_TOPIC", "")
TIME_RE = re.compile(r"^\d{1,2}:\d{2}")

# Yapıdan bağımsız okuma: ekranda görünen, saat gibi duran hücreleri bulur,
# yatay konumuna göre İş Günleri / Cumartesi / Pazar sütununa atar.
JS = r"""
([title, other]) => {
  const R = x => JSON.stringify(x);
  const clean = s => s.replace(/\s+/g, ' ').trim();
  const norm = s => clean(s).toLocaleUpperCase('tr');
  const t = norm(title), o = norm(other);
  const cellRe = /^\d{1,2}:\d{2}(\s*\(-?\d\))?(\s*[A-Z])?$/;
  const labels = ['İŞ GÜNLERİ', 'CUMARTESİ', 'PAZAR'];
  const box = e => { const r = e.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2, w: r.width, h: r.height}; };
  const visible = e => { const b = box(e); return b.w > 0 && b.h > 0; };

  const all = [...document.querySelectorAll('body *')];
  const heads = all.filter(e => norm(e.textContent) === t && visible(e));
  if (!heads.length) return R({error: 'gorunen baslik bulunamadi', total: all.filter(e => norm(e.textContent) === t).length});
  const leafs = heads.filter(h => !heads.some(x => x !== h && h.contains(x)));
  const head = leafs[0];

  let node = head.parentElement;
  let lastInfo = '';
  while (node) {
    if (norm(node.textContent).includes(o)) break;
    const els = [...node.querySelectorAll('*')].filter(visible);
    const lab = labels.map(l => { const m = els.filter(e => norm(e.textContent) === l); return m.length ? m[m.length - 1] : null; });
    const cells = els.filter(e => cellRe.test(clean(e.textContent)) &&
      !(e.parentElement && cellRe.test(clean(e.parentElement.textContent))));
    lastInfo = 'etiket=' + lab.filter(Boolean).length + ' hucre=' + cells.length;
    if (lab.every(Boolean) && cells.length) {
      const xs = lab.map(l => box(l).x);
      const days = [[], [], []];
      cells.map(c => ({c, p: box(c)}))
        .sort((a, b) => a.p.y - b.p.y || a.p.x - b.p.x)
        .forEach(i => {
          let k = 0, best = 1e9;
          xs.forEach((x, n) => { const d = Math.abs(x - i.p.x); if (d < best) { best = d; k = n; } });
          days[k].push(clean(i.c.textContent));
        });
      return R({days, text: node.innerText});
    }
    node = node.parentElement;
  }
  const ctx = head.parentElement && head.parentElement.parentElement
    ? head.parentElement.parentElement.outerHTML : head.outerHTML;
  return R({error: 'tablo bulunamadi (' + lastInfo + ')', debug: ctx.slice(0, 3000)});
}
"""
OTHER_SECTION = "ÇİFTEÇINARLAR KALKIŞ"


def parse_text(text):
    """Yedek yöntem: innerText'te hücreler sekmeyle, satırlar yeni satırla ayrılır."""
    days = [[], [], []]
    for line in text.split("\n"):
        parts = [x.strip() for x in line.split("\t")]
        if len(parts) == 3 and all(x == "" or TIME_RE.match(x) for x in parts) and any(parts):
            for i, x in enumerate(parts):
                if x:
                    days[i].append(x)
    return days


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1200})
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector(f"text={SECTION}", timeout=30000)
        page.wait_for_timeout(3000)
        expr = "(" + JS + ")(" + json.dumps([SECTION, OTHER_SECTION]) + ")"
        res = json.loads(page.evaluate(expr))
        body_text = page.inner_text("body")
        browser.close()

    if "error" in res:
        print("--- DEBUG ---", res)
        print("--- DEBUG: SAYFA METNİ ---")
        i = body_text.upper().find("BAYRAKTAR")
        print(repr(body_text[max(0, i - 200): i + 800]))
        raise RuntimeError(f"Tablo okunamadı: {res['error']}")

    days = res["days"]
    text_days = parse_text(res.get("text", ""))
    print("Konum yöntemi:", [len(d) for d in days], "| Metin yöntemi:", [len(d) for d in text_days])
    # Daha çok saat bulan yöntemi kullan
    if sum(len(d) for d in text_days) > sum(len(d) for d in days):
        days = text_days

    result = dict(zip(DAYS, days))
    for day, times in result.items():
        print(f"{day} ({len(times)}): {', '.join(times)}")

    if not any(result.values()):
        raise RuntimeError("Tablo bulundu ama hiç saat okunamadı.")
    return result


def notify(message):
    print(message)
    if not TOPIC:
        print("(NTFY_TOPIC tanımlı değil, bildirim gönderilmedi)")
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{TOPIC}",
        data=message.encode("utf-8"),
        headers={"Title": "15SD Bayraktar Bulvari", "Tags": "bus"},
    )
    urllib.request.urlopen(req, timeout=30)


def main():
    new = scrape()

    if not STATE.exists():
        STATE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
        counts = ", ".join(f"{d}: {len(t)} sefer" for d, t in new.items())
        notify(f"Takip başladı. Okunan saatler: {counts}")
        return

    old = json.loads(STATE.read_text(encoding="utf-8"))
    lines = []
    for day in DAYS:
        o, n = set(old.get(day, [])), set(new.get(day, []))
        added, removed = sorted(n - o), sorted(o - n)
        if added or removed:
            lines.append(f"{day}:")
            if added:
                lines.append("  Eklendi: " + ", ".join(added))
            if removed:
                lines.append("  Kaldırıldı: " + ", ".join(removed))

    if lines:
        notify("Saatler değişti\n" + "\n".join(lines))
        STATE.write_text(json.dumps(new, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print("Değişiklik yok.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"HATA: {e}", file=sys.stderr)
        sys.exit(1)
