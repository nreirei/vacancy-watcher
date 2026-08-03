#!/usr/bin/env python3
"""
JKKねっと ダンプ v5 : ポップアップを捕捉する

判明した仕組み:
  中継ページの submitNext() は
    window.open("/search/jkknet/wait.jsp", "JKKnet") で新しいウィンドウを開き、
    forwardForm の target をそのウィンドウにして POST する。
  つまり本物の検索画面はポップアップ側に出る。元のページは中継ページのまま。

なので expect_popup() でポップアップを捕まえてダンプする。
"""

import os
import time

from playwright.sync_api import sync_playwright

PC = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
MOBILE = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenInitMobile"
PC_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
         "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def dump(page, label):
    log(f"\n{'='*45}\n### {label}\n{'='*45}")
    log(f"URL: {page.url}")
    try:
        log(f"title: {page.title()}")
    except Exception:
        pass

    for fi, frame in enumerate(page.frames):
        log(f"\n--- frame[{fi}] name={frame.name or '(none)'} url={frame.url[:90]}")
        try:
            forms = frame.locator("form")
            log(f"  forms: {forms.count()}")
            for i in range(min(forms.count(), 5)):
                f = forms.nth(i)
                log(f"    form[{i}] name={f.get_attribute('name')} "
                    f"method={f.get_attribute('method')} action={f.get_attribute('action')}")
        except Exception:
            pass

        try:
            inputs = frame.locator("input")
            n = inputs.count()
            log(f"  inputs: {n}")
            for i in range(min(n, 90)):
                el = inputs.nth(i)
                log(f"    [{i}] type={el.get_attribute('type')} "
                    f"name={el.get_attribute('name')} "
                    f"value={el.get_attribute('value')} "
                    f"onclick={(el.get_attribute('onclick') or '')[:70]}")
            if n > 90:
                log(f"    ...ほか{n-90}件")
        except Exception:
            pass

        try:
            sels = frame.locator("select")
            log(f"  selects: {sels.count()}")
            for i in range(min(sels.count(), 15)):
                s = sels.nth(i)
                opts = s.locator("option")
                sample = [f"{opts.nth(j).get_attribute('value')}={opts.nth(j).inner_text()}"
                          for j in range(min(opts.count(), 30))]
                log(f"    select[{i}] name={s.get_attribute('name')} n={opts.count()}")
                log(f"      {sample}")
        except Exception:
            pass

        try:
            links = frame.locator("a")
            n = links.count()
            log(f"  links: {n}")
            for i in range(min(n, 35)):
                el = links.nth(i)
                log(f"    a[{i}] {(el.inner_text() or '').strip()[:25]} "
                    f"href={(el.get_attribute('href') or '')[:60]} "
                    f"onclick={(el.get_attribute('onclick') or '')[:70]}")
        except Exception:
            pass

        try:
            log(f"  --- body ---\n{frame.locator('body').inner_text()[:2500]}")
        except Exception:
            pass


def try_entry(p, label, url):
    log(f"\n\n########## {label} ##########")
    browser = p.chromium.launch()
    ctx = browser.new_context(locale="ja-JP", user_agent=PC_UA,
                              viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.on("dialog", lambda d: d.dismiss())

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        time.sleep(3)

        log("submitNext() を呼んでポップアップを待つ...")
        try:
            with page.expect_popup(timeout=30000) as pi:
                page.evaluate("submitNext()")
            popup = pi.value
        except Exception as e:
            log(f"expect_popup 失敗: {e}")
            # 既に開いているページを探す
            pages = [pg for pg in ctx.pages if pg != page]
            if not pages:
                log("ポップアップは見つかりませんでした")
                return
            popup = pages[-1]

        log(f"ポップアップ取得: {popup.url}")

        # wait.jsp から本物の画面に切り替わるまで待つ
        for i in range(20):
            time.sleep(2)
            if "wait.jsp" not in popup.url:
                break
            log(f"  待機中... ({i+1}) {popup.url}")

        try:
            popup.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
        time.sleep(2)

        log(f"最終URL: {popup.url}")
        dump(popup, f"{label} のポップアップ")

    except Exception as e:
        import traceback
        log(f"!!! {label} 失敗: {type(e).__name__}: {str(e)[:300]}")
        log(traceback.format_exc()[:1200])
    finally:
        browser.close()


def main():
    with sync_playwright() as p:
        try_entry(p, "PC版", PC)
        try_entry(p, "モバイル版", MOBILE)


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
            f.write("# JKKねっと ダンプ v5\n\n```\n" + text + "\n```\n")
