
## Icon Reimport

After editing sheets in ``2_Output_Models/_ICONS/sheets (EDIT THESE)``, reimport with:

1) ``python start.py --patch-icons``
2) Patched output is written to ``3_Output_Dat/dt_na.dat``
3) A report is generated at ``2_Output_Models/_ICONS/metadata/reimport_report.json``

``python start.py --patch-icons --dry-run`` validates and reports without writing to output.

## Icons for unused characters and new characters

Icons for the six unused characters and for new character IDs come from the roster expansion (menu **[10]**, see [RosterGuide.md](RosterGuide.md)):

1) Put a side and a front portrait PNG per character into `1_Input/_Icons/`. They are fitted into 48x51 (`fit`: `contain`, `cover` or `strict`).
2) Name them in the character's entry of `1_Input/roster.json`: `"icon": {"side": "x_side.png", "front": "x_front.png"}`, or `"icon": "placeholder"` for the built-in "empty slot" portrait. Preset [2] already does this for the six unused characters with the PNGs shipped in `1_Input/_Icons/`.
3) Run menu [10] and choose your config ([u]) or a preset, then copy `main.dol`, `dt_na.dat` and `fst.bin` into the game.

No Gecko code is needed: the unused characters become selectable through their wheel entries.

The older route, `python start.py --add-custom-icons`, still exists but is superseded. Don't combine the two: once menu [10] has rebuilt the icon bank, `--add-custom-icons` refuses that output.
