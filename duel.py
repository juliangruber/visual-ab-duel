#!/usr/bin/env python3
"""A blind pairwise tournament for picking between visual options (single elimination with byes): two pictures per
match, full frame on a black canvas, labelled only "match M, picture N", in random pairing and order.

  uv run --with pillow python duel.py --state DIR init CANDIDATES.json [--canvas 3840x2160]
  uv run --with pillow python duel.py --state DIR next [--no-open]     make (or reopen) the current match's pictures
  uv run --with pillow python duel.py --state DIR record N             the user preferred picture N (1 or 2)
  uv run --with pillow python duel.py --state DIR defer                too close: the pair comes back after another match
  uv run --with pillow python duel.py --state DIR reshuffle            the same pair too close again: pair all still in anew
                                                                       (refused with only two left: moments, clips)
  uv run --with pillow python duel.py --state DIR moments [--no-open]  the current pair at the candidates' other moments
  uv run --with pillow python duel.py --state DIR clips [--no-open]    the current pair's clips, blind names
  uv run --with pillow python duel.py --state DIR status               progress, without names
  uv run --with pillow python duel.py --state DIR reveal [--force]     every match with names (at the end)

CANDIDATES.json: a list of {"id": ..., "label": ..., "image": path} (paths relative to the JSON's folder or
absolute), optionally "moments": [{"name": "1:40", "image": path}, ...] and "clip": path (a short movie).
The state (state.json, the match pictures mNN_1.png and mNN_2.png) lives in DIR; --state may also come from the
DUEL_STATE environment variable. Pictures are pasted 1:1, centered, untouched (scaled down to fit only if larger
than the canvas; 16-bit gray rounded to 8 bits); the label is dim gray in a bar outside the picture, or left out when
there is none (init --label over draws it over the picture then, --label none never).
"""
import argparse, json, os, random, shutil, subprocess, sys

RNG = random.SystemRandom()


# ------------------------------------------------------------------ state

def load(d):
    p = os.path.join(d, "state.json")
    if not os.path.exists(p):
        sys.exit("duel: no tournament in %s (run init)" % d)
    return json.load(open(p))


def save(d, st):
    p = os.path.join(d, "state.json")
    json.dump(st, open(p + ".tmp", "w"), indent=1)
    os.replace(p + ".tmp", p)


def is_ph(c):
    return c.startswith("?")   # the winner of a deferred match, not decided yet


def blocked(st, item):
    return isinstance(item, list) and any(is_ph(c) and c in st["pending"] for c in item)


def pair_round(st, entrants, shuffle=True):
    entrants = list(entrants)
    if shuffle:
        RNG.shuffle(entrants)
    alone = len(entrants) == 1   # only a deferred match's placeholder (its replay is the round): no bye
    bye = entrants.pop() if len(entrants) % 2 else None
    st["round"] += 1
    pairs = [entrants[i:i + 2] for i in range(0, len(entrants), 2)]
    plain = [p for p in pairs if not blocked(st, p)]
    waiting = [p for p in pairs if blocked(st, p)]
    replays = [{"replay": ph} for ph in st["replays"]]
    st["replays"] = []
    # a deferred pair comes back after one other match; pairs waiting for its winner come last
    st["queue"] = plain[:1] + replays + plain[1:] + waiting
    st["winners"] = [bye] if bye else []
    if bye and not alone:
        st["byes"].append(dict(round=st["round"], id=bye))
    st["current"] = None
    st["current_replay"] = None


def byes(st):
    """the byes as {"round", "id"} (a state from an earlier version kept bare ids, one per round, None for none)"""
    return [b if isinstance(b, dict) else dict(round=k + 1, id=b) for k, b in enumerate(st["byes"]) if b]


def swap(st, ph, win):
    st["winners"] = [win if c == ph else c for c in st["winners"]]
    st["byes"] = [dict(b, id=win) if b["id"] == ph else b for b in byes(st)]
    st["queue"] = [[win if c == ph else c for c in it] if isinstance(it, list) else it for it in st["queue"]]


def alive(st):
    out = {h["loser"] for h in st["history"] if "loser" in h}
    return [c for c in st["candidates"] if c not in out]


def done(st):
    return st["current"] is None and not st["queue"] and len(st["winners"]) == 1 and not st["pending"] and not st["replays"]


# ------------------------------------------------------------------ pictures

def font(size):
    from PIL import ImageFont
    for f in ("/System/Library/Fonts/Helvetica.ttc", "/Library/Fonts/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(f):
            return ImageFont.truetype(f, size)
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def picture(src_path, out, label, canvas_size, place="bar"):
    """src 1:1 (or scaled down to fit), centered on a black canvas, the label dim gray in a bar outside the picture
    (below it, else above, else beside it at the bottom); returns notes for the agent (never shown to the user).
    place: bar (default; with no bar wide enough the label is left out, the picture always untouched), over (with no
    bar, drawn over the picture's bottom left) or none (never drawn: the file name m07_1.png carries the numbers)"""
    from PIL import Image, ImageChops, ImageDraw
    notes = []
    src = Image.open(src_path)
    if src.mode.startswith("I"):   # 16-bit gray: rounded to 8 bits (no dither)
        src = src.convert("I").point(lambda v: v / 257 + 0.5).convert("L")
        notes.append("is 16-bit: rounded to 8 bits without dither; give the delivered 8-bit picture for a fair duel")
    elif any(";16" in str(t[3]) for t in getattr(src, "tile", None) or [] if len(t) > 3):
        notes.append("is 16-bit color: Pillow keeps only each sample's high byte (v // 256, not v / 257 rounded: up to "
                     "1 level of 255 off, on average about 0.4 darker in the shadows and 0.45 brighter near white); "
                     "convert it to 8 bits (rounded or dithered) first")
    mode = "L" if src.mode in ("L", "1", "LA") else "RGB"
    src = src.convert(mode)
    W, H = canvas_size
    if src.width > W or src.height > H:
        k = min(W / src.width, H / src.height)
        src = src.resize((max(1, round(src.width * k)), max(1, round(src.height * k))), Image.LANCZOS)
        notes.append("is scaled to %dx%d to fit the canvas" % src.size)
    canvas = Image.new(mode, (W, H), 0)
    x0, y0 = (W - src.width) // 2, (H - src.height) // 2
    canvas.paste(src, (x0, y0))
    size = max(12, round(H / 54))
    f = font(size)
    d = ImageDraw.Draw(canvas)
    fill = 90 if mode == "L" else (90, 90, 90)
    l, t, r, b = d.textbbox((0, 0), label, font=f)
    tw, th = r - l, b - t
    m = max(20, round(W / 96))
    below, above, side = H - (y0 + src.height), y0, x0
    pos = None
    if place != "none":
        if below >= th + 2 * max(4, th // 4):
            pos = (m, y0 + src.height + (below - th) // 2 - t)
        elif above >= th + 2 * max(4, th // 4):
            pos = (m, (above - th) // 2 - t)
        elif side >= tw + 16:
            pos = ((side - tw) // 2, H - th - max(8, th // 2) - t)
    if pos:
        d.text(pos, label, fill=fill, font=f)
    elif place == "over":
        d.text((m, H - th - round(size * 0.6) - t), label, fill=fill, font=f)
        notes.append("has no bar beside it: the label is drawn over its bottom left (--label over)")
    elif place == "bar":
        notes.append("has no bar beside it wide enough for the label: the label is left out (the picture untouched); "
                     "the match and picture numbers are only in the file name, which Preview shows in its title bar")
    if not (place == "over" and not pos):
        assert ImageChops.difference(canvas.crop((x0, y0, x0 + src.width, y0 + src.height)), src).getbbox() is None  # untouched
    canvas.save(out)
    return "; ".join(notes)


def open_files(files, app="Preview"):
    if sys.platform == "darwin":
        subprocess.run(["open", "-a", app] + files if app else ["open"] + files, check=False)
    else:
        for f in files:
            subprocess.run(["xdg-open", f], check=False)


# ------------------------------------------------------------------ commands

def cmd_init(d, a):
    if os.path.exists(os.path.join(d, "state.json")):
        sys.exit("duel: a tournament exists in %s; use another folder or move state.json away" % d)
    base = os.path.dirname(os.path.abspath(a.candidates))
    cands = json.load(open(a.candidates))
    ids = [c["id"] for c in cands]
    if len(set(ids)) != len(ids) or len(ids) < 2:
        sys.exit("duel: candidates need unique ids, at least two")
    if any(is_ph(i) for i in ids):
        sys.exit("duel: ids may not start with '?'")
    info = {}
    for c in cands:
        absp = lambda p: p if os.path.isabs(p) else os.path.join(base, p)
        if not os.path.exists(absp(c["image"])):
            sys.exit("duel: %s: %s does not exist" % (c["id"], absp(c["image"])))
        info[c["id"]] = dict(label=c.get("label", c["id"]), image=absp(c["image"]),
                             moments=[dict(name=m["name"], image=absp(m["image"])) for m in c.get("moments", [])],
                             clip=absp(c["clip"]) if c.get("clip") else None)
    W, H = (int(v) for v in a.canvas.lower().split("x"))
    os.makedirs(d, exist_ok=True)
    st = dict(round=0, match=0, history=[], byes=[], candidates=ids, info=info, canvas=[W, H], label=a.label, pending={},
              replays=[], queue=[], winners=[], current=None, current_replay=None)
    pair_round(st, ids)
    save(d, st)
    print("%d options, %d matches (more if some are too close); canvas %dx%d" % (len(ids), len(ids) - 1, W, H))
    cmd_next(d, a)


def cmd_next(d, a):
    st = load(d)
    if st["current"] is None:
        if done(st):
            sys.exit("duel: done, %d matches; run reveal" % st["match"])
        if not st["queue"]:
            pair_round(st, st["winners"])
        if all(blocked(st, it) for it in st["queue"]) and st["replays"]:   # only matches waiting for a replay
            st["queue"] = [{"replay": ph} for ph in st["replays"]] + st["queue"]
            st["replays"] = []
        i = next(k for k, it in enumerate(st["queue"]) if not blocked(st, it))
        item = st["queue"].pop(i)
        if isinstance(item, dict):
            st["current_replay"] = item["replay"]
            pair = list(st["pending"][item["replay"]])
        else:
            st["current_replay"] = None
            pair = list(item)
        RNG.shuffle(pair)
        st["match"] += 1
        st["current"] = pair
        save(d, st)
    m = st["match"]
    files = [os.path.join(d, "m%02d_%d.png" % (m, n)) for n in (1, 2)]
    for n, (c, f) in enumerate(zip(st["current"], files), 1):
        if not os.path.exists(f):
            note = picture(st["info"][c]["image"], f, "match %d, picture %d" % (m, n), st["canvas"], st.get("label", "bar"))
            if note:
                print("note: picture %d %s" % (n, note))
    if not getattr(a, "no_open", False):
        open_files(files)
    again = ""
    if st["current_replay"]:
        last = st["history"][-1] if st["history"] else {}
        again = ", the pair of match %d again%s" % (
            next(h["match"] for h in st["history"] if h.get("deferred") == st["current_replay"]),
            " (right after it: no other match could be played first)" if last.get("deferred") == st["current_replay"] else "")
    print("match %d (round %d)%s: %s" % (m, st["round"], again, "  ".join(files)))


def cmd_record(d, a):
    st = load(d)
    if not st["current"]:
        sys.exit("duel: no match open (run next)")
    n = a.n
    if n not in (1, 2):
        sys.exit("duel: record 1 or 2")
    win, lose = st["current"][n - 1], st["current"][2 - n]
    st["history"].append(dict(match=st["match"], round=st["round"], shown=st["current"], winner=win, loser=lose))
    ph = st["current_replay"]
    if ph:
        del st["pending"][ph]
        swap(st, ph, win)
    else:
        st["winners"].append(win)
    st["current"], st["current_replay"] = None, None
    save(d, st)
    left = len(alive(st)) - 1
    print("recorded match %d: picture %d; %s" % (st["match"], n, "done: run reveal" if done(st) else
          "%d options still in, at least %d match%s left" % (left + 1, left, "" if left == 1 else "es")))


def comes_right_back(st):
    """True if no other match can be played before the current pair would come back after a defer"""
    if any(not blocked(st, it) for it in st["queue"]):
        return False
    if st["queue"] or st["replays"]:   # only matches waiting for a replay, or other replays first
        return not st["replays"]
    # the next round: other matches only if at least two options in it are decided (not placeholders)
    return len([c for c in st["winners"] if not is_ph(c)]) < 2


def cmd_defer(d, a):
    st = load(d)
    if not st["current"]:
        sys.exit("duel: no match open")
    before = [h["match"] for h in st["history"] if "deferred" in h and set(h["shown"]) == set(st["current"])]
    if before and not a.force:
        if len(alive(st)) <= 2:   # a reshuffle would only pair these two again
            sys.exit("duel: this pair was found too close before (match %s) and only these two are left, so a reshuffle "
                     "cannot pair them differently: show them at other moments or as clips (moments, clips) and ask "
                     "again; if they stay too close, they are equivalent for the user: let them decide on other "
                     "grounds (or keep the current version). defer --force shows the same pair again"
                     % ", ".join(map(str, before)))
        sys.exit("duel: this pair was found too close before (match %s); too close again: run reshuffle (or show "
                 "other moments or clips first: moments, clips; or defer --force)" % ", ".join(map(str, before)))
    if comes_right_back(st) and not a.force:
        sys.exit("duel: no other match can be played before this pair would come back (%s): do not defer; show the "
                 "pair at other moments or as clips (moments, clips) and ask again%s. Without moments or clips: defer "
                 "--force shows the same pair again as the next match, or treat them as equivalent"
                 % ("only these two are left" if len(alive(st)) == 2 else "too few options left in this round",
                    "" if len(alive(st)) == 2 else ", or reshuffle (the options still in are paired anew, avoiding this pair)"))
    immediate = comes_right_back(st)
    ph = st["current_replay"] or "?%d" % st["match"]
    if not st["current_replay"]:
        st["pending"][ph] = st["current"]
        st["winners"].append(ph)
    st["history"].append(dict(match=st["match"], round=st["round"], shown=st["current"], deferred=ph))
    # back after one other match: after the next match that can be played now, else in the next round
    free = [k for k, it in enumerate(st["queue"]) if not blocked(st, it)]
    if free:
        st["queue"].insert(free[0] + 1, {"replay": ph})
    else:
        st["replays"].append(ph)
    st["current"], st["current_replay"] = None, None
    save(d, st)
    print("match %d deferred: %s" % (st["match"], "no other match can be played first, so the pair comes back right "
          "away as the next match" if immediate else "the pair comes back after another match" if free else
          "the pair comes back in the next round (if that round has no match that can be played first, right away: "
          "next then says 'right after it')"))


def cmd_reshuffle(d, a):
    st = load(d)
    if len(alive(st)) <= 2 and not a.force:
        sys.exit("duel: only two options are left: a reshuffle would pair them again. Show them at other moments or as "
                 "clips (moments, clips), or let the user decide (reshuffle --force to do it anyway)")
    if st["current"]:
        st["history"].append(dict(match=st["match"], round=st["round"], shown=st["current"], deferred="reshuffled"))
    still = alive(st)
    if st["current"] or st["queue"] or st["pending"] or st["replays"]:
        # the round is cut short: its pairs and its bye are dropped (the bye took no one further)
        st["byes"] = [b for b in byes(st) if b["round"] != st["round"]]
    st.update(pending={}, replays=[], current=None, current_replay=None, queue=[], winners=[])
    close = [set(h["shown"]) for h in st["history"] if "deferred" in h]
    for _ in range(500):  # a draw that splits every pair found too close, if there is one
        RNG.shuffle(still)
        if not any(set(still[i:i + 2]) in close for i in range(0, len(still) - 1, 2)):
            break
    pair_round(st, still, shuffle=False)
    save(d, st)
    if any(isinstance(p, list) and set(p) in close for p in st["queue"]):
        print("note: a pair found too close could not be avoided (too few options left): show other moments or clips")
    print("reshuffled the %d options still in into round %d: at least %d matches left" % (len(still), st["round"], len(still) - 1))


def cmd_moments(d, a):
    st = load(d)
    if not st["current"]:
        sys.exit("duel: no match open")
    m = st["match"]
    names = [mo["name"] for mo in st["info"][st["current"][0]]["moments"]]
    if not names:
        sys.exit("duel: the candidates have no moments")
    files = []
    for k, name in enumerate(names, 1):
        for n, c in enumerate(st["current"], 1):
            mo = {x["name"]: x["image"] for x in st["info"][c]["moments"]}
            if name not in mo:
                sys.exit("duel: a candidate has no moment %s" % name)
            f = os.path.join(d, "m%02d_moment%d_%d.png" % (m, k, n))
            note = picture(mo[name], f, "match %d, picture %d (%s)" % (m, n, name), st["canvas"], st.get("label", "bar"))
            if note:
                print("note: moment %d picture %d %s" % (k, n, note))
            files.append(f)
    if not a.no_open:
        open_files(files)
    print("match %d at %d other moment%s (picture 1 then 2 for each): %s" % (m, len(names), "" if len(names) == 1 else "s",
                                                                          "  ".join(files)))


def cmd_clips(d, a):
    st = load(d)
    if not st["current"]:
        sys.exit("duel: no match open")
    m = st["match"]
    files = []
    for n, c in enumerate(st["current"], 1):
        src = st["info"][c]["clip"]
        if not src:
            sys.exit("duel: a candidate has no clip")
        f = os.path.join(d, "m%02d_clip_%d%s" % (m, n, os.path.splitext(src)[1]))
        shutil.copyfile(src, f)
        files.append(f)
    if not a.no_open:
        open_files(files, app=None)
    print("match %d as clips: %s" % (m, "  ".join(files)))


def cmd_status(d, a):
    st = load(d)
    decided = len([h for h in st["history"] if "winner" in h])
    deferred = len([h for h in st["history"] if "deferred" in h])
    n = len(alive(st))
    print("round %d, match %d: %d decided, %d too close, %d option%s still in, match open: %s%s" % (
        st["round"], st["match"], decided, deferred, n, "" if n == 1 else "s", "yes" if st["current"] else "no",
        "; done" if done(st) else ""))


def cmd_reveal(d, a):
    st = load(d)
    if not done(st) and not a.force:
        sys.exit("duel: not finished; revealing ends the blind test (reveal --force)")
    L = lambda c: st["info"][c]["label"] if c in st["info"] else c
    for h in st["history"]:
        if "deferred" in h:
            print("match %d (round %d): too close (%s), %s  vs  %s" % (h["match"], h["round"], "reshuffled" if h["deferred"] == "reshuffled" else "deferred", L(h["shown"][0]), L(h["shown"][1])))
        else:
            n = h["shown"].index(h["winner"]) + 1
            print("match %d (round %d): picture %d, %s  over  %s" % (h["match"], h["round"], n, L(h["winner"]), L(h["loser"])))
    shown = ["%s (round %d)" % (L(b["id"]), b["round"]) for b in byes(st) if not is_ph(b["id"])]
    if shown:
        print("byes:", ", ".join(shown))
    if done(st):
        print("winner:", L(st["winners"][0]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=os.environ.get("DUEL_STATE"), help="the tournament's folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init")
    p.add_argument("candidates")
    p.add_argument("--canvas", default="3840x2160")
    p.add_argument("--label", default="bar", choices=["bar", "over", "none"],
                   help="bar: in a bar outside the picture, left out if there is none (default); over: then over the "
                   "picture's bottom left; none: never (the file names carry the numbers)")
    p.add_argument("--no-open", action="store_true")
    p = sub.add_parser("next")
    p.add_argument("--no-open", action="store_true")
    p = sub.add_parser("record")
    p.add_argument("n", type=int)
    p = sub.add_parser("defer")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("reshuffle")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("moments")
    p.add_argument("--no-open", action="store_true")
    p = sub.add_parser("clips")
    p.add_argument("--no-open", action="store_true")
    sub.add_parser("status")
    p = sub.add_parser("reveal")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if not a.state:
        sys.exit("duel: give --state DIR (or set DUEL_STATE)")
    d = os.path.abspath(a.state)
    globals()["cmd_" + a.cmd](d, a)


if __name__ == "__main__":
    main()
