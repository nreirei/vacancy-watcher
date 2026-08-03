#!/usr/bin/env python3
"""
JKKねっと 画面構造ダンプ v2

最初のページが「自動POSTで次画面へ飛ぶ中継ページ」だと判明したので、
その飛び先URL・Cookie・フォーム送信後の状態を詳しく調べる。
"""

import os
import time

from playwright.sync_api import sync_playwright

START = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def is_interstitial(frame):
    """まだ中継ページにいるかどうか"""
    try:
        return "自動で次の画面" in frame.locator("body").inner_text()
    except Exception:
        return False


def dump(page, label):
    log(f"\n{'='*50}")
    log(f"### {label}")
    log(f"{'='*50}")
    log(f"URL: {page.url}")
    try:
        log(f"title: {page.title()}")
    except Exception:
        pass

    for fi, frame in enumerate(page.frames):
        log(f"\n--- frame[{fi}] name={frame.name or '(none)'} ---")
        log(f"    url: {frame.url}")

        try:
            forms = frame.locator("form")
            log(f"    forms: {forms.count()}")
            for i in range(min(forms.count(), 5)):
                f = forms.nth(i)
                log(
                    f"      form[{i}] name={f.get_attribute('name')} "
                    f"method={f.get_attribute('method')} "
                    f"action={f.get_attribute('action')}"
                )
        except Exception as e:
            log(f"    forms error: {e}")

        try:
            inputs = frame.locator("input")
            n = inputs.count()
            log(f"    inputs: {n}")
            for i in range(min(n, 80)):
                el = inputs.nth(i)
                t = el.get_attribute("type")
                nm = el.get_attribute("name")
                val = el.get_attribute("value")
                oc = el.get_attribute("onclick")
                # 値は省略せず全部出す（飛び先URLを知りたいため）
                log(f"      input[{i}] type={t} name={nm} value={val} onclick={oc}")
            if n > 80:
                log(f"      ...ほか{n-80}件")
        except Exception as e:
            log(f"    inputs error: {e}")

        try:
            sels = frame.locator("select")
            log(f"    selects: {sels.count()}")
            for i in range(min(sels.count(), 12)):
                s = sels.nth(i)
                opts = s.locator("option")
                sample = []
                for j in range(min(opts.count(), 10)):
                    sample.append(
                        f"{opts.nth(j).get_attribute('value')}={opts.nth(j).inner_text()}"
                    )
                log(f"      select[{i}] name={s.get_attribute('name')} {sample}")
        except Exception as e:
            log(f"    selects error: {e}")

        try:
            links = frame.locator("a")
            n = links.count()
            log(f"    links: {n}")
            for i in range(min(n, 40)):
                el = links.nth(i)
                txt = (el.inner_text() or "").strip().replace("\n", " ")[:30]
                href = (el.get_attribute("href") or "")[:90]
                oc = (el.get_attribute("onclick") or "")[:90]
                log(f"      a[{i}] text={txt} href={href} onclick={oc}")
        except Exception as e:
            log(f"    links error: {e}")

        try:
            body = frame.locator("body").inner_text()[:2000]
            log(f"    --- body ---\n{body}")
        except Exception:
            pass


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(
            locale="ja-JP",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        # ネットワーク遷移を記録する
        page.on("framenavigated",
                lambda f: log(f"[nav] {f.url}") if f == page.main_frame else None)

        log("### STEP 1: 中継ページを開く")
        page.goto(START, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)

        # 飛び先URLを完全な形で取得
        target = None
        try:
            target = page.locator("input[name=url]").first.get_attribute("value")
            log(f"\n★ 飛び先URL(完全): {target}")
        except Exception as e:
            log(f"飛び先URL取得失敗: {e}")

        log(f"\n★ Cookie: {ctx.cookies()}")
        log(f"★ 中継ページか: {is_interstitial(page.main_frame)}")

        log("\n### STEP 2: フォームをJSで送信してみる")
        try:
            page.evaluate("document.forwardForm.submit()")
            page.wait_for_load_state("networkidle", timeout=30000)
            time.sleep(3)
        except Exception as e:
            log(f"submit失敗: {e}")
        log(f"送信後URL: {page.url}")
        log(f"まだ中継ページか: {is_interstitial(page.main_frame)}")
        if not is_interstitial(page.main_frame):
            dump(page, "STEP2 成功: 条件入力画面")
        else:
            log("→ まだ中継ページ。STEP3へ")

            log("\n### STEP 3: 飛び先URLへ直接アクセス")
            if target:
                try:
                    page.goto(target, wait_until="networkidle", timeout=60000)
                    time.sleep(3)
                    log(f"アクセス後URL: {page.url}")
                    log(f"中継ページか: {is_interstitial(page.main_frame)}")
                    dump(page, "STEP3 の画面")
                except Exception as e:
                    log(f"直接アクセス失敗: {e}")

            log("\n### STEP 4: 公式サイトの入口から辿る")
            try:
                page.goto(
                    "https://www.to-kousya.or.jp/chintai/index.html",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                time.sleep(3)
                links = page.locator("a")
                found = []
                for i in range(min(links.count(), 300)):
                    href = links.nth(i).get_attribute("href") or ""
                    txt = (links.nth(i).inner_text() or "").strip().replace("\n", " ")
                    if "jhomes" in href or "akiya" in href.lower():
                        found.append(f"{txt[:30]} -> {href[:110]}")
                log("公式サイト内のJKKねっとへのリンク:")
                for f in found[:25]:
                    log(f"  {f}")
                if not found:
                    log("  見つかりませんでした")
            except Exception as e:
                log(f"公式サイト探索失敗: {e}")

        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        log(f"\n!!! 例外: {type(e).__name__}: {e}")
        log(traceback.format_exc()[:2000])

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        text = "\n".join(out)
        if len(text) > 60000:
            text = text[:60000] + "\n...(切り詰め)"
        with open(path, "a", encoding="utf-8") as f:
            f.write("# JKKねっと 画面構造ダンプ v2\n\n```\n" + text + "\n```\n")
