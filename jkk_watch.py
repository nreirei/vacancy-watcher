#!/usr/bin/env python3
"""
JKK東京（東京都住宅供給公社）あき家監視スクリプト

JKKねっとのあき家検索を定期実行し、前回から増えた部屋を
ntfy.sh 経由でスマホにプッシュ通知する。

JKKねっとの仕組み（実地調査で判明）:
  1. 入口URLは中継ページ。submitNext() を呼ぶとポップアップが開く
  2. ポップアップに条件入力画面 (form name=akiSearch) が出る
  3. 地域は checkbox name="akiyaInitRM.akiyaRefM.checks" value=<コード>
  4. 検索実行は submitPage('akiyaJyoukenRef')
  5. 結果はタブ区切り9列の表

使い方:
    python jkk_watch.py --dry-run --debug
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
from playwright.sync_api import sync_playwright

START = "https://jhomes.to-kousya.or.jp/search/jkknet/service/akiyaJyoukenStartInit"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "state.json"
DEBUG_DIR = ROOT / "debug"

# 区市町村 → JKKねっとの内部コード（旧行政区画コードに由来）
AREA_CODES = {
    # 区部
    "千代田区": "01", "中央区": "02", "港区": "03", "新宿区": "04",
    "文京区": "05", "台東区": "06", "墨田区": "07", "江東区": "08",
    "品川区": "09", "目黒区": "10", "大田区": "11", "世田谷区": "12",
    "渋谷区": "13", "中野区": "14", "杉並区": "15", "豊島区": "16",
    "北区": "17", "荒川区": "18", "板橋区": "19", "練馬区": "20",
    "足立区": "21", "葛飾区": "22", "江戸川区": "23",
    # 市部
    "八王子市": "31", "立川市": "32", "武蔵野市": "33", "三鷹市": "34",
    "青梅市": "35", "府中市": "36", "昭島市": "37", "調布市": "38",
    "町田市": "39", "小金井市": "40", "小平市": "41", "日野市": "42",
    "東村山市": "43", "国分寺市": "44", "国立市": "45",
    "西東京市": "46-47", "福生市": "48", "狛江市": "49",
    "東大和市": "50", "清瀬市": "51", "東久留米市": "52",
    "武蔵村山市": "53", "多摩市": "54", "稲城市": "55",
    "羽村市": "57", "あきる野市": "56-64",
    # 町村
    "瑞穂町": "62", "日の出町": "63", "檜原村": "65", "奥多摩町": "66",
}


# --------------------------------------------------------------------------
# 設定・状態
# --------------------------------------------------------------------------
def load_config():
    with open(ROOT / "config.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    def csv_env(key):
        v = os.environ.get(key, "").strip()
        return [s.strip() for s in v.split(",") if s.strip()] if v else None

    if csv_env("JKK_AREAS"):
        cfg["areas"] = csv_env("JKK_AREAS")
    if os.environ.get("JKK_KEYWORDS") is not None:
        cfg["property_keywords"] = csv_env("JKK_KEYWORDS") or []
    if os.environ.get("JKK_LAYOUTS") is not None:
        cfg["layouts"] = csv_env("JKK_LAYOUTS") or []
    if os.environ.get("JKK_MAX_RENT", "").strip():
        cfg["max_rent"] = int(os.environ["JKK_MAX_RENT"])
    if os.environ.get("NTFY_TOPIC", "").strip():
        cfg["notify"]["topic"] = os.environ["NTFY_TOPIC"]
    return cfg


def load_state():
    if STATE_FILE.exists():
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"seen": []}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def room_hash(rid):
    salt = os.environ.get("JKK_SALT", "jkk-watcher")
    return hashlib.sha256(f"{salt}|{rid}".encode("utf-8")).hexdigest()[:16]


def mask(text, show):
    if show:
        return text
    if not text:
        return "-"
    return text[0] + "●" * max(len(text) - 1, 1)


# --------------------------------------------------------------------------
# スクレイピング
# --------------------------------------------------------------------------
def open_search_form(page):
    """中継ページからポップアップの条件入力画面を開く"""
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


def select_areas(popup, areas):
    """指定された区市町村のチェックボックスを選択する"""
    ok = 0
    for area in areas:
        code = AREA_CODES.get(area.strip())
        if not code:
            print(f"[warn] 未知の地域名: {area}", file=sys.stderr)
            continue
        try:
            box = popup.locator(
                f'input[name="akiyaInitRM.akiyaRefM.checks"][value="{code}"]'
            )
            box.first.check(timeout=10000)
            ok += 1
        except Exception as e:
            print(f"[warn] {area} のチェック失敗: {e}", file=sys.stderr)
    return ok


def parse_results(popup):
    """結果一覧の表から部屋情報を取り出す。

    データ行は 住宅名/地域/優先種別/住宅種別/間取り/床面積/家賃/共益費/募集戸数 の9列。
    入れ子テーブルの外枠 tr を除外するため、table を含む tr は無視する。
    """
    rooms = []
    rows = popup.locator("tr")

    for i in range(rows.count()):
        row = rows.nth(i)
        try:
            # 入れ子の外枠行は飛ばす
            if row.locator("table").count() > 0:
                continue
            cells = row.locator("xpath=./td")
            vals = []
            for j in range(cells.count()):
                v = cells.nth(j).inner_text(timeout=2000)
                vals.append(re.sub(r"\s+", "", v))
        except Exception:
            continue

        if len(vals) < 8:
            continue

        # 「◯◯区」「◯◯市」のセルを見つけて基準にする
        idx = None
        for j, v in enumerate(vals):
            if re.fullmatch(r".{1,6}[区市町村]", v):
                idx = j
                break
        if idx is None or idx == 0:
            continue

        rest = vals[idx + 1:]
        if len(rest) < 6:
            continue

        name = vals[idx - 1]
        if not name:
            continue

        area = vals[idx]
        yusen, jtype, madori, menseki, yachin, kyoueki = rest[:6]
        kosu = rest[6] if len(rest) > 6 else ""

        # 家賃が数値でない行はヘッダなど
        rent = parse_rent(yachin)
        if rent is None:
            continue

        # 住宅コード（senPage の第2引数）を取れれば ID に使う
        code = ""
        try:
            for k in range(row.locator("a").count()):
                oc = row.locator("a").nth(k).get_attribute("onclick") or ""
                m = re.search(r"senPage\('[^']*','([^']+)','([^']+)','([^']*)'", oc)
                if m:
                    code = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                    break
        except Exception:
            pass

        rooms.append({
            "id": code or f"{name}|{madori}|{menseki}|{yachin}|{yusen}",
            "name": name,
            "area": area,
            "yusen": yusen,
            "type": jtype,
            "layout": madori,
            "menseki": menseki,
            "rent": rent,
            "rent_text": yachin,
            "kyoueki": kyoueki,
            "count": kosu,
            "raw": " ".join(vals)[:200],
        })

    return rooms


def parse_rent(text):
    """「218,600」「266,400～286,400」から下限の家賃を円で返す"""
    if not text:
        return None
    m = re.search(r"([\d,]{4,})", text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return None


def fetch_listings(areas, debug=False):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(locale="ja-JP", user_agent=UA,
                                  viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
        page.on("dialog", lambda d: d.accept())

        popup = open_search_form(page)

        n = select_areas(popup, areas)
        print(f"[info] 地域を{n}件選択")
        if n == 0:
            raise RuntimeError("地域を1つも選択できませんでした。JKK_AREASの表記を確認してください。")

        popup.evaluate("submitPage('akiyaJyoukenRef')")
        try:
            popup.wait_for_load_state("networkidle", timeout=60000)
        except Exception:
            pass
        time.sleep(4)

        body = ""
        try:
            body = popup.locator("body").inner_text()
        except Exception:
            pass

        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            popup.screenshot(path=str(DEBUG_DIR / "results.png"), full_page=True)
            (DEBUG_DIR / "results.html").write_text(popup.content(), encoding="utf-8")

        if "該当" in body and re.search(r"0件が該当", body):
            print("[info] 該当0件")
            browser.close()
            return []

        rooms = parse_results(popup)
        browser.close()
        return rooms


# --------------------------------------------------------------------------
# フィルタ・通知
# --------------------------------------------------------------------------
def apply_filters(rooms, cfg):
    kws = cfg.get("property_keywords") or []
    max_rent = cfg.get("max_rent") or 0
    layouts = cfg.get("layouts") or []

    out = []
    for r in rooms:
        if kws and not any(k in r["name"] for k in kws):
            continue
        if max_rent and r["rent"] and r["rent"] > max_rent:
            continue
        if layouts and not any(l in r["layout"] for l in layouts):
            continue
        out.append(r)
    return out


def notify(cfg, new_rooms):
    n = cfg["notify"]
    url = f"{n.get('server', 'https://ntfy.sh').rstrip('/')}/{n['topic']}"

    lines = []
    for r in new_rooms[:10]:
        lines.append(
            f"・{r['name']} {r['area']}\n"
            f"  {r['layout']} {r['menseki']}m2 {r['rent_text']}円 "
            f"({r['count']}戸)"
        )
    if len(new_rooms) > 10:
        lines.append(f"…ほか{len(new_rooms)-10}件")

    resp = requests.post(
        url,
        data="\n".join(lines).encode("utf-8"),
        headers={
            "Title": f"JKK空室 {len(new_rooms)}件".encode("utf-8"),
            "Priority": n.get("priority", "high"),
            "Tags": "house",
            "Click": START,
        },
        timeout=30,
    )
    resp.raise_for_status()
    print(f"[notify] {len(new_rooms)}件を通知しました")


# --------------------------------------------------------------------------
# サマリ
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
            f"- 取得: **{len(all_rooms)}件**\n"
            f"- 条件一致: **{len(matched)}件**\n"
            f"- 新着: **{len(new_rooms)}件**\n"
        )

    if not all_rooms and not error:
        md.append("\n> 該当0件、またはパースに失敗した可能性があります。\n")

    if matched:
        if not show:
            md.append("\n> 🔒 物件名は伏せています（このページは公開されています）。\n")
        md.append("\n| | 住宅名 | 間取り | 家賃 | 戸数 |\n|---|---|---|---|---|\n")
        for r in matched:
            mk = "🆕" if r.get("hash") not in seen else ""
            md.append(
                f"| {mk} | {mask(r['name'], show)} | {r['layout']} "
                f"| {r['rent_text']} | {r['count']} |\n"
            )

    with open(path, "a", encoding="utf-8") as f:
        f.write("".join(md))


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--show-details", action="store_true",
                    help="ログに物件名を出す（公開リポジトリでは非推奨）")
    args = ap.parse_args()
    show = args.show_details

    cfg = load_config()
    state = load_state()
    seen = set(state.get("seen", []))

    try:
        all_rooms = fetch_listings(cfg["areas"], debug=args.debug)
    except Exception as e:
        write_summary([], [], [], seen, error=f"{type(e).__name__}: {e}", show=show)
        raise

    print(f"[info] 取得: {len(all_rooms)}件")
    rooms = apply_filters(all_rooms, cfg)
    print(f"[info] 条件一致: {len(rooms)}件")

    for r in rooms:
        r["hash"] = room_hash(r["id"])

    current = {r["hash"] for r in rooms}
    new_rooms = [r for r in rooms if r["hash"] not in seen]

    for r in rooms:
        mk = "NEW" if r["hash"] not in seen else "   "
        print(f"  {mk} {mask(r['name'], show)} {r['layout']} {r['rent_text']}")

    write_summary(all_rooms, rooms, new_rooms, seen, show=show)

    if new_rooms and not args.dry_run:
        notify(cfg, new_rooms)
    elif not new_rooms:
        print("[info] 新着なし")

    state["seen"] = sorted(current)
    if not args.dry_run:
        save_state(state)


if __name__ == "__main__":
    main()
