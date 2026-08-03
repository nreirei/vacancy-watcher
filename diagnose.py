#!/usr/bin/env python3
"""
JKKねっと ダンプ v6 : 検索を実行して結果一覧の構造を調べる

v5で判明した仕組み:
  1. 中継ページで submitNext() を呼ぶとポップアップ(名前 JKKnet)が開く
  2. ポップアップに条件入力画面が出る (form name=akiSearch)
  3. 地域は checkbox name="akiyaInitRM.akiyaRefM.checks" value=<区コード>
     区コードは東京都の行政区画コードと一致（板橋区=19 など）
  4. 検索実行は submitPage('akiyaJyoukenRef') を呼ぶ

このスクリプトでは、プライバシーのため特定の区ではなく
「区部すべて」(allCheck=ALLKU) で検索し、結果一覧の構造だけを調べる。
"""

import os
import time

from playwright.sync_api import sync_playwright

PC = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def open_search_popup(ctx, page):
    """中継ページからポップアップの条件入力画面を取得する"""
    page.goto(PC, wait_until="domcontentloaded", timeout=60000)
    time.sleep(3)
    with page.expect_popup(timeout=30000) as pi:
        page.evaluate("submitNext()")
    popup = pi.value
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

        popup = open_search_popup(ctx, page)
        popup.on("dialog", lambda d: d.accept())
        log(f"条件入力画面: {popup.url}")
        log(f"title: {popup.title()}")

        # --- 区部すべてにチェック ---
        log("\n区部すべてにチェックを入れる")
        try:
            popup.locator("input[value=ALLKU]").first.check(timeout=10000)
            time.sleep(1)
            checked = popup.evaluate(
                "document.querySelectorAll("
                "'input[name=\"akiyaInitRM.akiyaRefM.checks\"]:checked').length"
            )
            log(f"チェックされた区の数: {checked}")
        except Exception as e:
            log(f"チェック失敗: {e}")

        # --- 検索実行 ---
        log("\nsubmitPage('akiyaJyoukenRef') を実行")
        try:
            popup.evaluate("submitPage('akiyaJyoukenRef')")
        except Exception as e:
            log(f"submitPage失敗: {e}")
        try:
            popup.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            pass
        time.sleep(4)

        log(f"\n検索後URL: {popup.url}")
        log(f"title: {popup.title()}")

        # --- 結果ページの構造 ---
        log("\n" + "=" * 45)
        log("### 結果一覧ページの構造")
        log("=" * 45)

        try:
            tables = popup.locator("table")
            log(f"tables: {tables.count()}")
        except Exception:
            pass

        try:
            rows = popup.locator("tr")
            n = rows.count()
            log(f"tr の総数: {n}")
            log("\n--- 各行のテキスト（最大40行）---")
            for i in range(min(n, 40)):
                try:
                    t = rows.nth(i).inner_text(timeout=2000)
                    t = " | ".join(x.strip() for x in t.split("\n") if x.strip())
                    log(f"  tr[{i}]: {t[:220]}")
                except Exception:
                    pass
        except Exception as e:
            log(f"行取得失敗: {e}")

        # --- リンク（詳細ページへの遷移方法）---
        try:
            links = popup.locator("a")
            n = links.count()
            log(f"\nlinks: {n}")
            for i in range(min(n, 30)):
                el = links.nth(i)
                log(f"  a[{i}] text={(el.inner_text() or '').strip()[:25]} "
                    f"href={(el.get_attribute('href') or '')[:60]} "
                    f"onclick={(el.get_attribute('onclick') or '')[:80]}")
        except Exception:
            pass

        # --- 本文 ---
        try:
            log(f"\n--- body text ---\n{popup.locator('body').inner_text()[:4000]}")
        except Exception:
            pass

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
            f.write("# JKKねっと ダンプ v6（結果一覧）\n\n```\n" + text + "\n```\n")
