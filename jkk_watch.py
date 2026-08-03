#!/usr/bin/env python3
"""
JKK東京（東京都住宅供給公社）あき家監視スクリプト

JKKねっとのあき家検索を定期的に確認し、前回から増えた部屋を
ntfy.sh 経由でスマホにプッシュ通知する。

使い方:
    python jkk_watch.py            # 通常実行
    python jkk_watch.py --dry-run  # 通知せず結果だけ表示
    python jkk_watch.py --debug    # スクショとHTMLを debug/ に保存
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

import requests
import yaml
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
DEBUG_DIR = ROOT / "debug"


# --------------------------------------------------------------------------
# 設定・状態
# --------------------------------------------------------------------------
def load_config():
    """config.yml を読み、環境変数（GitHub Secrets）があれば上書きする。

    パブリックリポジトリでは、住みたいエリアや団地名が公開されないよう
    実際の条件は Secrets 側に置き、config.yml にはダミー値だけを残す。
    """
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    def csv_env(key):
        v = os.environ.get(key, "").strip()
        if not v:
            return None
        return [s.strip() for s in v.split(",") if s.strip()]

    if csv_env("JKK_AREAS"):
        cfg["areas"] = csv_env("JKK_AREAS")
    if csv_env("JKK_KEYWORDS") is not None:
        cfg["property_keywords"] = csv_env("JKK_KEYWORDS")
    if csv_env("JKK_LAYOUTS") is not None:
        cfg["layouts"] = csv_env("JKK_LAYOUTS")
    if os.environ.get("JKK_MAX_RENT", "").strip():
        cfg["max_rent"] = int(os.environ["JKK_MAX_RENT"])
    if os.environ.get("NTFY_TOPIC", "").strip():
        cfg["notify"]["topic"] = os.environ["NTFY_TOPIC"]

    return cfg


def room_hash(room_id):
    """state.json には部屋名をそのまま書かず、ハッシュだけを保存する。

    公開リポジトリのコミット履歴から、監視対象や過去の空室状況が
    読み取られないようにするため。
    """
    salt = os.environ.get("JKK_SALT", "jkk-watcher")
    return hashlib.sha256(f"{salt}|{room_id}".encode("utf-8")).hexdigest()[:16]


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------
# スクレイピング
# --------------------------------------------------------------------------
def fetch_listings(areas, debug=False):
    """JKKねっとのあき家検索を実行し、部屋のリストを返す。

    JKKねっとは Shift-JIS のセッション型フォームなので、実ブラウザで操作する。
    画面構成が変わるとセレクタがずれるため、失敗したら --debug でHTMLを確認すること。
    """
    rooms = []

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

        page.goto(BASE, wait_until="networkidle", timeout=60000)
        # 「しばらく経っても表示されない場合」用の自動遷移を待つ
        time.sleep(3)

        # --- 条件選択画面 ---------------------------------------------
        # 区市町村のチェックボックスを label のテキストで探して選択
        for area in areas:
            try:
                # ラベル文字列を含む要素の近くのチェックボックスをクリック
                box = page.get_by_role("checkbox", name=re.compile(area))
                if box.count() > 0:
                    box.first.check(timeout=5000)
                    continue
                # role で取れない古いHTML向けフォールバック
                page.locator(
                    f"xpath=//td[contains(., '{area}')]//input[@type='checkbox']"
                ).first.check(timeout=5000)
            except Exception as e:
                print(f"[warn] エリア「{area}」のチェックに失敗: {e}", file=sys.stderr)

        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "01_condition.png"), full_page=True)
            (DEBUG_DIR / "01_condition.html").write_text(
                page.content(), encoding="utf-8"
            )

        # --- 検索実行 --------------------------------------------------
        clicked = False
        for pattern in ["検索", "この条件で", "次へ"]:
            btn = page.get_by_role("button", name=re.compile(pattern))
            if btn.count() == 0:
                btn = page.locator(f"input[type=submit][value*='{pattern}']")
            if btn.count() > 0:
                btn.first.click()
                clicked = True
                break
        if not clicked:
            raise RuntimeError(
                "検索ボタンが見つかりません。--debug でHTMLを確認してください。"
            )

        page.wait_for_load_state("networkidle", timeout=60000)
        time.sleep(2)

        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "02_results.png"), full_page=True)
            (DEBUG_DIR / "02_results.html").write_text(page.content(), encoding="utf-8")

        # --- 結果パース ------------------------------------------------
        rooms = parse_results(page)

        browser.close()

    return rooms


def parse_results(page):
    """結果テーブルから部屋情報を抜き出す。

    列構成が変わりうるので、行のテキスト全体から正規表現で拾う方式にしている。
    """
    rooms = []
    rows = page.locator("tr")
    for i in range(rows.count()):
        try:
            text = rows.nth(i).inner_text(timeout=2000)
        except PWTimeout:
            continue

        text = re.sub(r"[ \t]+", " ", text).replace("\n", " ").strip()
        if not text:
            continue

        # 家賃らしき表記が無い行はヘッダ等とみなしてスキップ
        rent = extract_rent(text)
        if rent is None:
            continue

        room_no = ""
        m = re.search(r"([0-9A-Za-zｲ-ﾝア-ン\-ー]+号室)", text)
        if m:
            room_no = m.group(1)

        layout = ""
        m = re.search(r"(\d+[SLDK]{1,3})", text)
        if m:
            layout = m.group(1)

        # 行の先頭付近を住宅名とみなす
        name = text.split(" ")[0]

        rooms.append(
            {
                "id": f"{name}|{room_no}|{rent}",
                "name": name,
                "room": room_no,
                "rent": rent,
                "layout": layout,
                "raw": text[:200],
            }
        )
    return rooms


def extract_rent(text):
    """「7.1万円」「71,000円」等から家賃を円単位で取り出す"""
    m = re.search(r"([\d.]+)\s*万円", text)
    if m:
        try:
            return int(float(m.group(1)) * 10000)
        except ValueError:
            return None
    m = re.search(r"([\d,]{4,})\s*円", text)
    if m:
        try:
            return int(m.group(1).replace(",", ""))
        except ValueError:
            return None
    return None


# --------------------------------------------------------------------------
# フィルタ
# --------------------------------------------------------------------------
def apply_filters(rooms, cfg):
    kws = cfg.get("property_keywords") or []
    max_rent = cfg.get("max_rent") or 0
    layouts = cfg.get("layouts") or []

    out = []
    for r in rooms:
        if kws and not any(k in r["name"] or k in r["raw"] for k in kws):
            continue
        if max_rent and r["rent"] and r["rent"] > max_rent:
            continue
        if layouts and not any(l in r["raw"] for l in layouts):
            continue
        out.append(r)
    return out


# --------------------------------------------------------------------------
# 通知
# --------------------------------------------------------------------------
def notify(cfg, new_rooms):
    n = cfg["notify"]
    url = f"{n.get('server', 'https://ntfy.sh').rstrip('/')}/{n['topic']}"

    lines = []
    for r in new_rooms[:10]:
        rent = f"{r['rent']:,}円" if r["rent"] else "?"
        lines.append(f"・{r['name']} {r['room']} {r['layout']} {rent}")
    if len(new_rooms) > 10:
        lines.append(f"…ほか{len(new_rooms) - 10}件")

    body = "\n".join(lines)

    resp = requests.post(
        url,
        data=body.encode("utf-8"),
        headers={
            "Title": f"JKK空室 {len(new_rooms)}件".encode("utf-8"),
            "Priority": n.get("priority", "high"),
            "Tags": "house",
            "Click": BASE,
        },
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[notify] {len(new_rooms)}件を通知しました")


def mask(text, show):
    """公開ログに物件名や地名が出ないようにする。

    パブリックリポジトリでは Actions のログ・ジョブサマリ・Artifacts が
    すべて誰でも閲覧可能なため、既定では伏せ字にする。
    実際の物件名は ntfy の通知（自分だけが購読）で受け取る。
    """
    if show:
        return text
    if not text:
        return "-"
    return text[0] + "●" * max(len(text) - 1, 1)


# --------------------------------------------------------------------------
# GitHub Actions のジョブサマリ出力（スマホのブラウザで読める）
# --------------------------------------------------------------------------
def write_summary(all_rooms, matched, new_rooms, seen, error=None, show=False):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return

    md = ["# JKK空室監視の結果\n"]

    if error:
        md.append(f"## ❌ エラー\n\n```\n{error}\n```\n")
    else:
        md.append(
            f"- 取得できた部屋: **{len(all_rooms)}件**\n"
            f"- 条件に一致: **{len(matched)}件**\n"
            f"- うち新着: **{len(new_rooms)}件**\n"
        )

    if len(all_rooms) == 0 and not error:
        md.append(
            "\n> ⚠️ **1件も取得できていません。**\n"
            "> セレクタが画面に合っていない可能性が高いです。\n"
            "> 下の「取得したHTMLの一部」と、Artifacts の `debug-output` にある\n"
            "> `02_results.png` を確認してください。\n"
        )

    if matched:
        if not show:
            md.append(
                "\n> 🔒 物件名は伏せています（このページは公開されています）。\n"
                "> 実際の物件名は ntfy の通知をご覧ください。\n"
            )
        md.append("\n| | 住宅名 | 部屋 | 間取り | 家賃 |\n|---|---|---|---|---|\n")
        for r in matched:
            mark = "🆕" if r.get("hash", r["id"]) not in seen else ""
            rent = f"{r['rent']:,}円" if r["rent"] else "-"
            md.append(
                f"| {mark} | {mask(r['name'], show)} | {mask(r['room'], show)} "
                f"| {r['layout']} | {rent} |\n"
            )

    # パースできなかった場合の手掛かりとしてHTMLの冒頭を貼る
    # （地名を含むため、--show-details 指定時のみ）
    html_path = DEBUG_DIR / "02_results.html"
    if html_path.exists() and show:
        raw = html_path.read_text(encoding="utf-8", errors="replace")
        # スクリプトとスタイルを削って読みやすくする
        raw = re.sub(r"<(script|style)[\s\S]*?</\1>", "", raw, flags=re.I)
        raw = re.sub(r"\n{3,}", "\n\n", raw)
        md.append(
            "\n<details><summary>取得したHTMLの一部（タップで展開）</summary>\n\n"
            "```html\n" + raw[:6000] + "\n```\n\n</details>\n"
        )

    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(md))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="通知せず結果表示のみ")
    ap.add_argument("--debug", action="store_true", help="スクショとHTMLを保存")
    ap.add_argument(
        "--show-details",
        action="store_true",
        help="ログに物件名をそのまま出す（公開リポジトリでは非推奨）",
    )
    args = ap.parse_args()
    show = args.show_details

    cfg = load_config()
    state = load_state()
    seen = set(state.get("seen", []))

    try:
        all_rooms = fetch_listings(cfg["areas"], debug=args.debug)
    except Exception as e:
        write_summary([], [], [], seen, error=f"{type(e).__name__}: {e}")
        raise

    print(f"[info] 取得: {len(all_rooms)}件")

    rooms = apply_filters(all_rooms, cfg)
    print(f"[info] 条件一致: {len(rooms)}件")

    # 比較・保存はすべてハッシュで行う（state.json に地名を残さない）
    for r in rooms:
        r["hash"] = room_hash(r["id"])

    current = {r["hash"] for r in rooms}
    new_rooms = [r for r in rooms if r["hash"] not in seen]

    for r in rooms:
        mark = "NEW" if r["hash"] not in seen else "   "
        print(
            f"  {mark} {mask(r['name'], show)} {mask(r['room'], show)} "
            f"{r['layout']} {r['rent']}"
        )

    write_summary(all_rooms, rooms, new_rooms, seen, show=show)

    if new_rooms and not args.dry_run:
        notify(cfg, new_rooms)
    elif not new_rooms:
        print("[info] 新着なし")

    # 消えた部屋は seen から外す（再募集を拾えるように）
    state["seen"] = sorted(current)
    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()

