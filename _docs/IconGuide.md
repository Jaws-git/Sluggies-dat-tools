
## Icon Reimport

After editing sheets in ``2_Output_Models/_ICONS/sheets (EDIT THESE)``, reimport with:

1) ``python start.py --patch-icons``
2) Patched output is written to ``3_Output_Dat/dt_na.dat``
3) A report is generated at ``2_Output_Models/_ICONS/metadata/reimport_report.json``

``python start.py --patch-icons --dry-run`` validates and reports without writing to output.

## Icons for unused characters and new characters

Icons for the six unused characters and for new character IDs come from the roster expansion (menu **[9]**, and part of menu **[1]**; see [RosterGuide.md](RosterGuide.md)):

1) Put a side and a front portrait PNG per character into `1_Input/_Icons/`. They are fitted into 48x51 (`fit`: `contain`, `cover` or `strict`).
2) Name them in the character's entry of a roster configuration in `1_Input/_RosterConfigurations/`: `"icon": {"side": "x_side.png", "front": "x_front.png"}`. The presets' open slots use `empty_slot_side.png` / `empty_slot_front.png`. Preset [2] already does this for the six unused characters with the PNGs shipped in `1_Input/_Icons/`.
3) Run menu [9] (or [1]) and pick that configuration, then copy `main.dol`, `dt_na.dat` and `fst.bin` into the game.

No Gecko code is needed: the unused characters become selectable through their wheel entries.

The icon export (menu [1], or [3] after a roster injection) also writes the roster's two icon pages to `2_Output_Models/_ICONS/roster_pages/`, each named the way Dolphin dumps it (`tex1_WxH_<hash>_14.png`, also listed in `dolphin_icon_names.txt`). For Dolphin's custom textures, put an edited copy under exactly that name into Dolphin's `Load/Textures/RMBE01/` folder. The name depends on the roster configuration: which portraits a configuration packs decides the page's content and size, so export again after changing it.
