# Custom CPU Level Guide

The **Custom CPU** tab adds one extra CPU level to exhibition matches: a 5th
button on the game's "CPU Level" popup, next to Rookie, Minor, Major and
All-Star. You decide how it plays. The four stock levels stay exactly as
they are.

Technical reference: `_docs/_docs_custom_cpu/CustomCpuLevel.md`.

## Quick start

1. Run the normal export first, so `3_Output_Dat` holds the game. The
   original `main.dol` must stay in `1_Input`.
2. Open the **Custom CPU** tab and tick **Custom CPU level**.
3. Type a **button name** (1-25 characters). It is the caption shown when
   the 5th button is highlighted.
4. Turn switches on and change values (below).
5. Press **Deploy**. Nothing is written before that. With the grid's
   "Deploy patched files to game directory" ticked, the files are copied to
   the game directory afterwards.
6. In the game: Exhibition, then pick the 5th button on the CPU Level
   popup. It shows the All-Star picture with your caption.

To remove it again, untick **Custom CPU level** and press **Deploy**.

## How it behaves

- With every switch off and no value changed, the custom level plays
  exactly like All-Star. Everything you change is on top of that.
- Your values only apply during a match started with the 5th button. A
  match at a stock level afterwards plays the unchanged game.
- In a CPU vs CPU match both sides play the custom level.
- A level changed from the pause menu during a custom match goes back to
  the game's own behaviour.
- The button is only in the exhibition popup. Challenge mode and the
  minigames are untouched.

## The tab

- **Sections** on the left; a `*` marks a section with values that differ
  from All-Star.
- The **value table**: Setting, Custom (your value), Stock (level 4) and a
  Reset button per row. Hover a setting's name for what it does.
- Colours: orange differs from what is deployed (Deploy will change it),
  blue differs from All-Star, red is a problem. Deploy is refused while
  there is a problem; the reasons are listed under the table.
- **Discard pending edits** goes back to what is deployed. **Reset all**
  goes back to All-Star values.
- **Save settings...** and **Load settings...** write and read `.slgcpu`
  files. They use the settings format of the Slugout patcher, so files can
  be exchanged with it. A file with several level columns asks which one to
  load.

## Switches

Four **group switches** replace a whole set of the game's CPU values with
tuned ones. The values in the group's section then start from the tuned
value and can still be changed one by one.

| Switch | Section it tunes |
|---|---|
| Batters read your pitches | Batting |
| Pitchers charge, curve and move the ball | Pitching |
| Fielders dive, jump and react | Fielding |
| Runners tap and press fast | Running |

Eleven **behaviour switches** add new CPU behaviour:

| Switch | What the CPU does | Its settings |
|---|---|---|
| Pitchers learn each batter | Works the edges of the zone and learns during the game what gets each batter out | Pitch model, Pitch mix |
| Batters swing fully charged | Starts charging at once and is at full charge when it swings | |
| Batters see where the pitch really crosses | Judges ball or strike where the pitch crosses the plate, so breaking balls do not fool it | |
| Never bunts | Never bunts | |
| Charged pitches weaken your hits | Its charged pitches cut your hit power, as yours cut its | |
| Star swings by hitter strength | Weak hitters always star-swing with 2 or more stars; strong hitters rarely or never | Star swings |
| Never throws star pitches | The pitcher never throws a star pitch | |
| Smart rundown throws | Throws ahead at once and times throws behind the runner to get the out | Rundowns |
| Aims batting items at fielders | Uses its item, aimed and timed at the fielder about to get the ball | Items |
| Fielders handle items | Fielders jump POWs, pass when a teammate is near, run around items and ignore Boo | Items |
| Brings in its best relief pitchers | Picks its relief pitcher from its best and moves fielders for better chemistry | |

The settings of a behaviour only act while its switch is on.

## Good to know

- **Star usage** and **Preferred pitch** (odds per character class) used to
  be on the Gameplay tab for every level. They are custom level settings
  now; the stock levels play the game's own values. With "Star swings by
  hitter strength" on, the star usage odds are not used.
- **Pitchers learn each batter** works out each pitcher's starting pitch mix
  from the characters' stats when you deploy, and again at every roster
  run. After changing stats with the stat editor (Bridge), press **Deploy**
  again.
- **Aims batting items**: by default a quarter of the at-bats get some
  added timing and aim error. Set "Error-free item at-bats" to 100 for
  perfect throws.
- **Roster changes and game options** keep the custom level: it is rebuilt
  automatically after every roster run.
- **Gecko codes**: like the game options, the custom level adds the tools'
  own code area to `main.dol`. Gecko codes made for the stock game stop
  working then; the tools warn in red before the first such change.
- **Button name characters**: letters, digits and punctuation all show.
  Accented letters used by the game's own texts and `Ä Ö Ü ä ö ü ß Ñ` show
  too; other accented letters may show as gaps (you get a warning).

## Command line

```
py start.py --custom-cpu FILE.slgcpu [--cpu-level 4|5|6] [--cpu-button NAME] [--dry-run]
py start.py --custom-cpu-off [--dry-run]
py start.py --custom-cpu-save FILE.slgcpu
```

`--custom-cpu` turns the level on with a settings file (or replaces its
settings), `--custom-cpu-off` removes it, `--custom-cpu-save` writes the
deployed settings into a file.
