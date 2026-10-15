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

# Başlığı bulur, yukarı çıkarak 3 hücreli ve hücreleri saat olan satırları arar.
# tr/td de olsa, div tabanlı ızgara da olsa çalışır.
JS = """
([title, other]) => {
  const R = x => JSON.stringify(x);
  const norm = s => s.replace(/\\s+/g, ' ').trim().toLocaleUpperCase('tr');
  const timeRe = /^\\d{1,2}:\\d{2}/;
  const t = norm(title), o = norm(other);
  const all = [...document.querySelectorAll('body *')];
  const heads = all.filter(e => norm(e.textContent) === t);
  if (!heads.length) return R({rows: [], debug: 'baslik elemani bulunamadi'});
  const head = heads[heads.length - 1];

  const findRows = root => {
    const out = [];
    for (const el of root.querySelectorAll('*')) {
      if (el.children.length !== 3) continue;
      const cells = [...el.children].map(c => c.textContent.replace(/\\s+/g, ' ').trim());
      const ok = cells.every(c => c === '' || timeRe.test(c));
      if (ok && cells.some(c => c !== '')) out.push(cells);
    }
    return out;
  };

  let node = head.parentElement;
  while (node) {
    if (norm(node.textContent).includes(o)) break;
    const rows = findRows(node);
    if (rows.length) return R({rows, debug: ''});
    node = node.parentElement;
  }
  const ctx = head.parentElement && head.parentElement.parentElement
    ? head.parentElement.parentElement.outerHTML : head.outerHTML;
  return R({rows: [], debug: ctx.slice(0, 3000)});
}
"""
OTHER_SECTION = "ÇİFTEÇINARLAR KALKIŞ"


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector(f"text={SECTION}", timeout=30000)
        page.wait_for_timeout(3000)
        expr = "(" + JS + ")(" + json.dumps([SECTION, OTHER_SECTION]) + ")"
        res = json.loads(page.evaluate(expr))
        body_text = page.inner_text("body")
        browser.close()

    rows = res["rows"]
    if not rows:
        print("--- DEBUG: HTML ---")
        print(res["debug"])
        print("--- DEBUG: SAYFA METNİ ---")
        i = body_text.upper().find("BAYRAKTAR")
        print(body_text[max(0, i - 200): i + 800])
        raise RuntimeError("Tablo bulunamadı, sayfa yapısı değişmiş olabilir.")

    result = {}
    for i, day in enumerate(DAYS):
        result[day] = [r[i] for r in rows if TIME_RE.match(r[i])]

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
