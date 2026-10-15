---
name: visual-ab-duel
description: A blind pairwise tournament for picking between visual options (looks, film stocks, grades, renders, designs) - two full frame pictures per message labelled only "match M, picture N", random pairing and order, the user answers 1, 2 or too close, names revealed only at the end. Use when a user has to choose among several close visual options, when labelled or side by side comparisons would bias the choice, or when a long list of looks needs narrowing to one.
---

# Blind visual A/B duel

`duel.py` in this skill's folder runs the tournament (single elimination with byes, deferrals and reshuffles); it
needs Python 3 and pillow.

Below, `duel.py` stands for `uv run --with pillow python ~/.claude/skills/visual-ab-duel/duel.py --state DIR` (the
state folder; or set `DUEL_STATE`):

```sh
duel.py init candidates.json --canvas 3840x2160   # pairs everything at random, makes and opens match 1 [--label bar|over|none]
duel.py next           # make (or reopen) the current match's two pictures
duel.py record 1       # the user preferred picture 1 (or 2)
duel.py defer          # too close: this pair comes back after another match (refused if none can be played first)
duel.py reshuffle      # the same pair too close again: pair everything still in anew, avoiding pairs found too close
                       # (refused with only two left: it would pair them again)
duel.py moments        # the current pair at the candidates' other moments, blind, same picture numbers
duel.py clips          # the current pair's clips, copied to blind names
duel.py status         # progress, no names
duel.py reveal         # at the end: every match with names, and the winner (--force before the end)
```

`--no-open` (on init, next, moments, clips) only prints the paths instead of opening Preview: use it when the user is
not at this machine, then send or link the files.

## Setup

1. One picture per candidate, all of the same frame or moment, as delivered (final resolution, 8-bit). Fit the options
   to the same brightness first unless brightness is the question, or the duel picks the brighter one. Put the
   current version in as a candidate. 16-bit gray is rounded to 8 bits (no dither; the tool notes it); 16-bit color
   is read by Pillow as each sample's high byte (v // 256, not v / 257 rounded: up to 1 level of 255 off, on average
   about 0.4 darker in the shadows and 0.45 brighter near white; the tool notes it): convert both to 8 bits the way
   the delivery does, so every candidate is shown alike.
2. `candidates.json`: `[{"id": "now", "label": "the current look", "image": "posts/now.png", "moments": [{"name":
   "1:40", "image": "posts/now_1-40.png"}], "clip": "clips/now.mp4"}, ...]` (paths relative to the JSON or absolute;
   `moments` and `clip` optional, for pairs that are too close).
3. A state folder of the user's choice (it keeps `state.json` and the match pictures `mNN_1.png`, `mNN_2.png`; keep it,
   it is the record of the decision) and the canvas: the user's screen (3840x2160 for 4K). Pictures are pasted 1:1,
   centered, untouched (scaled down only if larger than the canvas; the tool says so), with "match M, picture N" in
   dim gray in a bar outside the picture: below it, else above, else beside it (a 4:3 picture on a 16:9 screen).
   A picture that fills the canvas (a 16:9 delivery on a 16:9 screen) has no bar: by default the label is then left
   out and the picture stays untouched, the numbers only in the file name (`m07_1.png`, in Preview's title bar);
   tell the user to read them there. `init --label over` draws the label over the picture's bottom left instead
   (covering a small corner of it); `--label none` never draws it.

## Protocol in chat

- **One pair per message.** "Match 7: picture 1 and picture 2 are open in Preview. Which do you prefer: 1, 2, or too
  close?" Nothing that could identify the options: no names, ids, which one is the current look, measurements, file
  names, or the round's structure. Never reveal names until the end, unless the user asks to stop.
- **Keep the numbering.** Matches count up over the whole tournament and picture numbers are per match; always use
  them exactly as the user sees them in the pictures.
- **1 or 2:** `record N`, then `next`, and send the next pair.
- **Too close:** `defer`, then `next`; tell the user that pair will come back after another match. If `defer` refuses
  because no other match can be played first: with three or more options still in, `reshuffle` (all of them are
  paired anew, avoiding this pair), then `next`, or go to `moments` and `clips`; with only these two left, go to
  `moments` and `clips` as below. If `next` says the pair is back "right after it" (it can happen at random with few
  options left), say so: "Match 9 is the pair from match 8 again: ...".
- **The same pair too close again:** `defer` refuses; with three or more options still in, run `reshuffle` (all of
  them are paired anew, avoiding pairs found too close), then `next`. With only these two left a reshuffle cannot
  help (both refuse and say so): go on as below.
- **Still too close, or only those two left:** `moments` (the pair at other moments, picture 1 then 2 for each, same
  numbering) and, if that does not settle it, `clips` (short clips in motion, rendered beforehand or for this pair
  only). If they stay too close, say they are equivalent for the user and let them choose on other grounds (or keep
  the current version). Without moments or clips, `defer --force` shows the same pair again as the next match.
- Comments with an answer ("2, the blacks look gray in 1") are recorded as the choice; note the comment for later
  without revealing anything.
- **At the end** (`record` says done): `reveal` and show every match with names and the winner, including the
  deferred and reshuffled pairs and the byes (with their rounds; a round cut short by a reshuffle has none); then ask
  what to do with the winner.

## Lessons

- A pair that is too close on a still often differs in motion: grain is a texture over time, and on a still the
  brighter picture wins. Offer other moments, then short clips (10 s at full resolution was enough for film grain); on a
  shared machine queue clip renders behind other sessions' work.
- Full resolution, 1:1, full screen, one picture at a time; never tiled or downscaled for brightness or grain.
- With many options (20 or more), narrow first with one full resolution image per option and the current version
  before every group, then duel the favourites; a duel of N options takes about N - 1 messages plus deferrals.
- Run `status` rather than reading `state.json` in front of the user: the state holds the names.
- To duel videos in motion, the video-compare-slider skill (if installed) shows each match blind in a synced wipe
  slider: see its SKILL.md, "Blind duels in the slider".
