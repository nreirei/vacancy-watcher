#!/usr/bin/env python3
"""
JKKねっとの画面構造を調べるための診断スクリプト。

実際にブラウザでアクセスし、そこにある フレーム / 入力欄 / ボタン / リンク を
すべて列挙してジョブサマリに出力する。
セレクタを正しく書き直すための材料を集めるのが目的。

個人情報は出力しない（画面の部品名だけを出す）ので、
公開リポジトリのログに出しても問題ない。
"""

import os
import time

from playwright.sync_api import sync_playwright

START = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"

out = []


def log(s=""):
    print(s)
    out.append(str(s))


def dump_frame(frame, depth=0):
    pad = "  " * depth
    log(f"\n{pad}=== FRAME: {frame.name or '(no name)'} ===")
    log(f"{pad}url: {frame.url}")

    try:
        title = frame.title()
        log(f"{pad}title: {title}")
    except Exception:
        pass

    # --- フォーム ---
    try:
        forms = frame.locator("form")
        log(f"{pad}forms: {forms.count()}")
        for i in range(min(forms.count(), 5)):
            action = forms.nth(i).get_attribute("action")
            method = forms.nth(i).get_attribute("method")
            name = forms.nth(i).get_attribute("name")
            log(f"{pad}  form[{i}] name={name} method={method} action={action}")
    except Exception as e:
        log(f"{pad}forms: error {e}")

    # --- input 要素 ---
    try:
        inputs = frame.locator("input")
        n = inputs.count()
        log(f"{pad}inputs: {n}")
        for i in range(min(n, 60)):
            el = inputs.nth(i)
            t = el.get_attribute("type")
            nm = el.get_attribute("name")
            val = el.get_attribute("value")
            oc = el.get_attribute("onclick")
            if val and len(val) > 40:
                val = val[:40] + "..."
            if oc and len(oc) > 60:
                oc = oc[:60] + "..."
            log(f"{pad}  input[{i}] type={t} name={nm} value={val} onclick={oc}")
        if n > 60:
            log(f"{pad}  ...ほか{n - 60}件")
    except Exception as e:
        log(f"{pad}inputs: error {e}")

    # --- select 要素 ---
    try:
        sels = frame.locator("select")
        log(f"{pad}selects: {sels.count()}")
        for i in range(min(sels.count(), 10)):
            nm = sels.nth(i).get_attribute("name")
            opts = sels.nth(i).locator("option")
            sample = []
            for j in range(min(opts.count(), 8)):
                sample.append(
                    f"{opts.nth(j).get_attribute('value')}:{opts.nth(j).inner_text()}"
                )
            log(f"{pad}  select[{i}] name={nm} options={sample}")
    except Exception as e:
        log(f"{pad}selects: error {e}")

    # --- button 要素 ---
    try:
        btns = frame.locator("button")
        log(f"{pad}buttons: {btns.count()}")
        for i in range(min(btns.count(), 20)):
            log(f"{pad}  button[{i}] text={btns.nth(i).inner_text()[:40]}")
    except Exception as e:
        log(f"{pad}buttons: error {e}")

    # --- リンク ---
    try:
        links = frame.locator("a")
        n = links.count()
        log(f"{pad}links: {n}")
        for i in range(min(n, 30)):
            el = links.nth(i)
            txt = (el.inner_text() or "").strip().replace("\n", " ")[:30]
            href = (el.get_attribute("href") or "")[:70]
            log(f"{pad}  a[{i}] text={txt} href={href}")
        if n > 30:
            log(f"{pad}  ...ほか{n - 30}件")
    except Exception as e:
        log(f"{pad}links: error {e}")

    # --- 本文テキストの冒頭 ---
    try:
        body = frame.locator("body").inner_text()[:1500]
        log(f"{pad}--- body text ---\n{body}")
    except Exception:
        pass

    for child in frame.child_frames:
        dump_frame(child, depth + 1)


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

        log("### STEP 1: 最初のページ")
        page.goto(START, wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        log(f"現在のURL: {page.url}")
        dump_frame(page.main_frame)

        # 「こちら」等の自動遷移リンクがあれば踏んでみる
        log("\n### STEP 2: リンクを踏んで次の画面へ")
        moved = False
        for frame in page.frames:
            try:
                links = frame.locator("a")
                for i in range(min(links.count(), 10)):
                    txt = (links.nth(i).inner_text() or "").strip()
                    if "こちら" in txt or "検索" in txt:
                        log(f"クリック: {txt}")
                        links.nth(i).click()
                        moved = True
                        break
            except Exception:
                continue
            if moved:
                break

        if moved:
            time.sleep(5)
            log(f"遷移後のURL: {page.url}")
            dump_frame(page.main_frame)
        else:
            log("踏めるリンクはありませんでした")

        browser.close()


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        log(f"\n!!! 例外: {type(e).__name__}: {e}")

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        text = "\n".join(out)
        # サマリの上限に配慮して切り詰める
        if len(text) > 60000:
            text = text[:60000] + "\n...(切り詰め)"
        with open(path, "a", encoding="utf-8") as f:
            f.write("# JKKねっと 画面構造ダンプ\n\n```\n" + text + "\n```\n")
