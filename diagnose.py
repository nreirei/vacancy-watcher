#!/usr/bin/env python3
"""
JKKねっと ダンプ v4 : 通信レベルの調査

v3で判明:
  - PC版もモバイル版も中継ページから進まない
  - submitNext() を呼んでも変化なし

そこで HTTP のやり取りそのものを記録する:
  - 全リクエスト/レスポンスのURL・メソッド・ステータス
  - コンソールエラー（JSが落ちていないか）
  - submitNext 関数が本当に存在するか
  - ビューポートを直して公式サイトのリンクを正しくクリック
"""

import os
import time

from playwright.sync_api import sync_playwright

OFFICIAL = "https://www.to-kousya.or.jp/chintai/index.html"
PC = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
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


def hook(page, tag):
    def on_req(r):
        if "to-kousya" in r.url:
            log(f"  [{tag} REQ ] {r.method} {r.url[:110]}")

    def on_res(r):
        if "to-kousya" in r.url:
            log(f"  [{tag} RES ] {r.status} {r.url[:110]}")

    page.on("request", on_req)
    page.on("response", on_res)
    page.on("console", lambda m: log(f"  [{tag} CONS] {m.type}: {m.text[:140]}"))
    page.on("pageerror", lambda e: log(f"  [{tag} ERR ] {str(e)[:200]}"))
    page.on("requestfailed",
            lambda r: log(f"  [{tag} FAIL] {r.url[:90]} {r.failure}"))


def inspect_js(page, tag):
    """中継ページのスクリプトの中身と関数の存在を確認"""
    try:
        exists = page.evaluate("typeof submitNext")
        log(f"  [{tag}] typeof submitNext = {exists}")
    except Exception as e:
        log(f"  [{tag}] evaluate失敗: {e}")
    try:
        scripts = page.evaluate(
            "Array.from(document.scripts).map(s => s.src || s.textContent).join('\\n---\\n')"
        )
        log(f"  [{tag}] --- scripts ---\n{scripts[:2500]}")
    except Exception as e:
        log(f"  [{tag}] script取得失敗: {e}")


def dump_page(page, label):
    log(f"\n{'='*45}\n### {label}\n{'='*45}")
    log(f"URL: {page.url}")
    log(f"中継ページか: {interstitial(page)}")
    for fi, frame in enumerate(page.frames):
        log(f"--- frame[{fi}] {frame.url[:90]}")
        try:
            forms = frame.locator("form")
            for i in range(min(forms.count(), 4)):
                f = forms.nth(i)
                log(f"  form[{i}] name={f.get_attribute('name')} action={f.get_attribute('action')}")
            inputs = frame.locator("input")
            log(f"  inputs: {inputs.count()}")
            for i in range(min(inputs.count(), 60)):
                el = inputs.nth(i)
                log(f"    [{i}] type={el.get_attribute('type')} name={el.get_attribute('name')} "
                    f"value={el.get_attribute('value')}")
            sels = frame.locator("select")
            log(f"  selects: {sels.count()}")
            for i in range(min(sels.count(), 12)):
                s = sels.nth(i)
                opts = s.locator("option")
                sample = [f"{opts.nth(j).get_attribute('value')}={opts.nth(j).inner_text()}"
                          for j in range(min(opts.count(), 12))]
                log(f"    select[{i}] name={s.get_attribute('name')} {sample}")
            log(f"  --- body ---\n{frame.locator('body').inner_text()[:1500]}")
        except Exception as e:
            log(f"  dump error: {e}")


def run(p, label, ua, viewport, action):
    log(f"\n\n########## {label} ##########")
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="ja-JP", user_agent=ua, viewport=viewport)
    page = ctx.new_page()
    hook(page, label[0])
    try:
        action(page)
        time.sleep(6)
        inspect_js(page, label[0])
        if interstitial(page):
            log(f"  [{label[0]}] 中継ページ → submitNext を実行")
            try:
                page.evaluate("submitNext()")
            except Exception as e:
                log(f"  submitNext例外: {e}")
            time.sleep(6)
        dump_page(page, label)
    except Exception as e:
        log(f"!!! {label} 失敗: {type(e).__name__}: {str(e)[:400]}")
    finally:
        browser.close()


def main():
    with sync_playwright() as p:

        def a(page):
            page.goto(OFFICIAL, wait_until="domcontentloaded", timeout=60000)
            time.sleep(3)
            # 非表示リンクは href を読んで直接遷移（Referer を付ける）
            href = page.locator("a[href*='akiyaJyoukenInitMobile']").first.get_attribute("href")
            log(f"  取得した href: {href}")
            page.evaluate("u => location.href = u", href)
            page.wait_for_load_state("domcontentloaded", timeout=60000)

        run(p, "A: 公式→モバイル版(スマホUA/スマホ画面)", SP_UA,
            {"width": 390, "height": 844}, a)

        def b(page):
            page.goto(PC, wait_until="domcontentloaded", timeout=60000)

        run(p, "B: PC版に直接(通信ログ重視)", PC_UA, {"width": 1280, "height": 900}, b)

        def c(page):
            page.goto(MOBILE, wait_until="domcontentloaded", timeout=60000)

        run(p, "C: モバイル版に直接(通信ログ重視)", SP_UA,
            {"width": 390, "height": 844}, c)


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
            f.write("# JKKねっと ダンプ v4\n\n```\n" + text + "\n```\n")
