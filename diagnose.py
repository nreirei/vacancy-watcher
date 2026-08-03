#!/usr/bin/env python3
"""
JKKねっと 練馬区の検索結果を検証する (v8)

背景:
  地域コード対応表は実物から取得して正しいことを確認済み（練馬区=20）。
  それでも練馬区が0件になる理由を特定する。

調べること:
  1. 練馬区のチェックが本当に入っているか（checked状態を確認）
  2. 検索実行後、JKKが何と返しているか（結果ページ本文をそのまま出力）
  3. 比較のため世田谷区でも同じことをやる（こちらは4件取れている）
  4. 全区チェックでの総件数と、練馬区の物件が含まれるか
"""

import os
import re
import time

from playwright.sync_api import sync_playwright

START = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def open_form(ctx):
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
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


def search(ctx, label, codes):
    log(f"\n{'='*55}")
    log(f"### {label}  codes={codes}")
    log("=" * 55)

    popup = open_form(ctx)

    # チェックを入れる
    for c in codes:
        try:
            popup.locator(
                f'input[name="akiyaInitRM.akiyaRefM.checks"][value="{c}"]'
            ).first.check(timeout=10000)
        except Exception as e:
            log(f"  チェック失敗 {c}: {e}")

    # 本当にチェックされたか確認
    checked = popup.evaluate("""
    () => Array.from(document.querySelectorAll(
        'input[name="akiyaInitRM.akiyaRefM.checks"]:checked'
    )).map(b => b.value)
    """)
    log(f"  実際にチェックされた値: {checked}")

    # 検索実行
    popup.evaluate("submitPage('akiyaJyoukenRef')")
    try:
        popup.wait_for_load_state("networkidle", timeout=60000)
    except Exception:
        pass
    time.sleep(4)

    log(f"  検索後URL: {popup.url}")

    body = ""
    try:
        body = popup.locator("body").inner_text()
    except Exception as e:
        log(f"  body取得失敗: {e}")

    # 件数表記を抜き出す
    m = re.search(r"(\d+)件が該当", body)
    log(f"  件数表記: {m.group(0) if m else '(見つからず)'}")

    if "ございませんでした" in body or "該当する住宅" in body:
        log("  → JKK側が『空室なし』と返している")

    # 結果行を出す
    try:
        rows = popup.locator("tr")
        n = rows.count()
        data = []
        for i in range(n):
            row = rows.nth(i)
            if row.locator("table").count() > 0:
                continue
            t = row.inner_text(timeout=2000)
            t = " | ".join(x.strip() for x in t.split("\t") if x.strip())
            if t and len(t) > 10:
                data.append(t[:180])
        log(f"  データ行候補: {len(data)}")
        for d in data[:25]:
            log(f"    {d}")
    except Exception as e:
        log(f"  行取得失敗: {e}")

    log(f"\n  --- 本文（先頭3000字） ---\n{body[:3000]}")
    popup.close()


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ja-JP", user_agent=UA,
                                  viewport={"width": 1280, "height": 900})

        # 1. 練馬区だけ
        search(ctx, "練馬区のみ", ["20"])

        # 2. 世田谷区だけ（取れている実績あり、比較用）
        search(ctx, "世田谷区のみ（比較対照）", ["12"])

        # 3. 区部すべて（練馬区の物件が含まれるか確認）
        search(ctx, "区部すべて", ["ALLKU"])

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
            f.write("# 練馬区の検証\n\n```\n" + text + "\n```\n")
