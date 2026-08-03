#!/usr/bin/env python3
"""
JKKねっと 画面構造ダンプ v3

判明したこと:
  - PC版 akiyaJyoukenStartInit は中継ページで自分自身に戻る無限ループ
  - 公式サイトに akiyaJyoukenInitMobile というモバイル版入口がある

そこで
  A: 公式サイトのリンクを実際にクリックして入る（Referer が付く）
  B: モバイル版へ直接アクセス
  C: モバイル端末のUAで開く
の3通りを試し、中継ページを突破できた画面をダンプする。
"""

import os
import time

from playwright.sync_api import sync_playwright

OFFICIAL = "https://www.to-kousya.or.jp/chintai/index.html"
MOBILE = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenInitMobile"

PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SP_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
         "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def interstitial(page):
    try:
        return "自動で次の画面" in page.locator("body").inner_text()
    except Exception:
        return False


def dump(page, label):
    log(f"\n{'='*45}")
    log(f"### {label}")
    log(f"{'='*45}")
    log(f"URL: {page.url}")
    log(f"中継ページか: {interstitial(page)}")

    for fi, frame in enumerate(page.frames):
        log(f"\n--- frame[{fi}] {frame.name or ''} {frame.url[:80]} ---")
        try:
            forms = frame.locator("form")
            for i in range(min(forms.count(), 4)):
                f = forms.nth(i)
                log(f"  form[{i}] name={f.get_attribute('name')} "
                    f"action={f.get_attribute('action')}")
        except Exception:
            pass

        try:
            inputs = frame.locator("input")
            n = inputs.count()
            log(f"  inputs: {n}")
            for i in range(min(n, 70)):
                el = inputs.nth(i)
                log(f"    [{i}] type={el.get_attribute('type')} "
                    f"name={el.get_attribute('name')} "
                    f"value={el.get_attribute('value')} "
                    f"onclick={(el.get_attribute('onclick') or '')[:70]}")
        except Exception:
            pass

        try:
            sels = frame.locator("select")
            log(f"  selects: {sels.count()}")
            for i in range(min(sels.count(), 12)):
                s = sels.nth(i)
                opts = s.locator("option")
                sample = []
                for j in range(min(opts.count(), 12)):
                    sample.append(f"{opts.nth(j).get_attribute('value')}={opts.nth(j).inner_text()}")
                log(f"    select[{i}] name={s.get_attribute('name')} {sample}")
        except Exception:
            pass

        try:
            links = frame.locator("a")
            n = links.count()
            log(f"  links: {n}")
            for i in range(min(n, 30)):
                el = links.nth(i)
                log(f"    a[{i}] {(el.inner_text() or '').strip()[:25]} "
                    f"href={(el.get_attribute('href') or '')[:70]} "
                    f"onclick={(el.get_attribute('onclick') or '')[:60]}")
        except Exception:
            pass

        try:
            log(f"  --- body ---\n{frame.locator('body').inner_text()[:1800]}")
        except Exception:
            pass


def attempt(p, label, ua, fn):
    log(f"\n\n########## {label} ##########")
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="ja-JP", user_agent=ua)
    page = ctx.new_page()
    try:
        fn(page)
        time.sleep(4)
        if interstitial(page):
            log("→ 中継ページのまま。submitNext を試す")
            try:
                page.evaluate("typeof submitNext==='function' ? submitNext() : document.forwardForm.submit()")
                time.sleep(5)
            except Exception as e:
                log(f"  submit失敗: {e}")
        dump(page, label)
    except Exception as e:
        log(f"!!! {label} 失敗: {type(e).__name__}: {e}")
    finally:
        browser.close()


def main():
    with sync_playwright() as p:

        # A: 公式サイトからモバイル版リンクをクリック
        def a(page):
            page.goto(OFFICIAL, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            link = page.locator("a[href*='akiyaJyoukenInitMobile']").first
            log(f"リンク件数: {page.locator('a[href*=akiyaJyoukenInitMobile]').count()}")
            link.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=60000)

        attempt(p, "A: 公式からモバイル版リンクをクリック(PC UA)", PC_UA, a)

        # B: モバイル版へ直接アクセス（スマホUA）
        def b(page):
            page.goto(MOBILE, wait_until="domcontentloaded", timeout=60000)

        attempt(p, "B: モバイル版へ直接アクセス(スマホ UA)", SP_UA, b)

        # C: 公式サイト経由でPC版（Referer付き）
        def c(page):
            page.goto(OFFICIAL, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            page.locator("a[href*='akiyaJyoukenStartInit']").first.click(timeout=15000)
            page.wait_for_load_state("domcontentloaded", timeout=60000)

        attempt(p, "C: 公式からPC版リンクをクリック(PC UA)", PC_UA, c)


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
            f.write("# JKKねっと ダンプ v3\n\n```\n" + text + "\n```\n")
