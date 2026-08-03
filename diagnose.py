#!/usr/bin/env python3
"""
JKKねっと 地域コード対応表の抽出 (v7)

目的:
  区市町村のチェックボックスと、その画面上のラベル文字列を
  「実物から」ペアで取り出す。
  これまでの対応表はDOM順から推測して作ったため、
  一部の区（練馬区など）でずれている疑いがある。

出力:
  そのまま jkk_watch.py の AREA_CODES に貼れる形の Python 辞書。
"""

import os
import time

from playwright.sync_api import sync_playwright

START = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def open_form(page):
    page.goto(START, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    with page.expect_popup(timeout=45000) as pi:
        page.evaluate("submitNext()")
    popup = pi.value
    popup.on("dialog", lambda d: d.accept())
    for _ in range(20):
        time.sleep(2)
        if "wait.jsp" not in popup.url:
            break
    try:
        popup.wait_for_load_state("networkidle", timeout=30000)
    except Exception:
        pass
    time.sleep(2)
    return popup


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ja-JP", user_agent=UA,
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())

        popup = open_form(page)
        log(f"条件入力画面: {popup.url}\n")

        # チェックボックスごとに、周辺のテキストをJSで直接拾う
        pairs = popup.evaluate("""
        () => {
          const boxes = document.querySelectorAll(
            'input[name="akiyaInitRM.akiyaRefM.checks"]');
          const res = [];
          boxes.forEach((b, i) => {
            let label = null;

            // 1. for属性で結びついた label
            if (b.id) {
              const l = document.querySelector('label[for="' + b.id + '"]');
              if (l) label = l.innerText;
            }
            // 2. 親が label
            if (!label && b.closest('label')) {
              label = b.closest('label').innerText;
            }
            // 3. 同じ td 内のテキスト
            if (!label) {
              const td = b.closest('td');
              if (td) label = td.innerText;
            }
            // 4. 次の兄弟ノード
            if (!label || !label.trim()) {
              let n = b.nextSibling, buf = '';
              while (n && buf.trim().length < 12) {
                buf += (n.textContent || '');
                n = n.nextSibling;
              }
              label = buf;
            }

            res.push({
              index: i,
              value: b.value,
              id: b.id || '',
              label: (label || '').replace(/\\s+/g, '').slice(0, 20),
              html: b.outerHTML.slice(0, 160)
            });
          });
          return res;
        }
        """)

        log(f"チェックボックス総数: {len(pairs)}\n")
        log("=" * 55)
        log("index | value  | label")
        log("=" * 55)
        for r in pairs:
            log(f"{r['index']:5d} | {r['value']:6s} | {r['label']}")

        log("\n" + "=" * 55)
        log("そのまま貼れる辞書形式")
        log("=" * 55)
        log("AREA_CODES = {")
        for r in pairs:
            lab = r["label"]
            if lab:
                log(f'    "{lab}": "{r["value"]}",')
            else:
                log(f'    # ラベル取得失敗: value={r["value"]} index={r["index"]}')
        log("}")

        # ラベルが取れなかった場合に備えて生HTMLも出す
        empty = [r for r in pairs if not r["label"]]
        if empty:
            log(f"\n!! ラベルを取得できなかった要素が {len(empty)} 件あります")
            for r in empty[:10]:
                log(f"  index={r['index']} value={r['value']}")
                log(f"    {r['html']}")

        # 検証: 練馬区が本当にどの値か、行単位のテキストでも確認する
        log("\n" + "=" * 55)
        log("検証: 地域欄のテーブル行のテキスト")
        log("=" * 55)
        try:
            rows = popup.evaluate("""
            () => {
              const b = document.querySelector(
                'input[name="akiyaInitRM.akiyaRefM.checks"]');
              const table = b.closest('table');
              return Array.from(table.querySelectorAll('tr')).map(
                tr => Array.from(tr.querySelectorAll('td')).map(td => {
                  const cb = td.querySelector('input[type=checkbox]');
                  return (cb ? '[' + cb.value + ']' : '') +
                         td.innerText.replace(/\\s+/g, '');
                }).join(' | ')
              );
            }
            """)
            for i, r in enumerate(rows[:20]):
                log(f"  tr[{i}]: {r[:200]}")
        except Exception as e:
            log(f"検証失敗: {e}")

        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"\n!!! 例外: {type(e).__name__}: {e}")
        log(traceback.format_exc()[:1500])

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        text = "\n".join(out)
        if len(text) > 60000:
            text = text[:60000] + "\n...(切り詰め)"
        with open(path, "a", encoding="utf-8") as f:
            f.write("# 地域コード対応表\n\n```\n" + text + "\n```\n")
