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

# Sayfada başlığı bulur, içinde satır (tr) olan ilk üst elemana çıkar, hücreleri okur.
JS = """
(title) => {
  const norm = s => s.replace(/\\s+/g, ' ').trim().toLocaleUpperCase('tr');
  const all = [...document.querySelectorAll('body *')];
  const head = all.find(e => e.children.length === 0 && norm(e.textContent) === norm(title));
  if (!head) return null;
  let node = head;
  while (node && !node.querySelector('tr')) node = node.parentElement;
  if (!node) return null;
  return [...node.querySelectorAll('tr')].map(tr =>
    [...tr.querySelectorAll('td')].map(td => td.textContent.replace(/\\s+/g, ' ').trim()));
}
"""


def scrape():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_selector(f"text={SECTION}", timeout=30000)
        page.wait_for_timeout(2000)
        rows = page.evaluate(JS, SECTION)
        browser.close()

    if not rows:
        raise RuntimeError("Tablo bulunamadı, sayfa yapısı değişmiş olabilir.")

    result = {}
    for i, day in enumerate(DAYS):
        result[day] = [r[i] for r in rows if len(r) > i and TIME_RE.match(r[i])]

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
