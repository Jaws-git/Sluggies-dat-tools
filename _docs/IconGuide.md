
## Icon Reimport

After editing sheets in ``2_Output_Models/_ICONS/sheets (EDIT THESE)``, reimport with:

1) ``python start.py --patch-icons``
2) Patched output is written to ``3_Output_Dat/dt_na.dat``
3) A report is generated at ``2_Output_Models/_ICONS/metadata/reimport_report.json``

``python start.py --patch-icons --dry-run`` validates and reports without writing to output.

## Icons for unused characters

1) insert your own front and side icon images into the templates in folder /1_Input/_Icons/
2) run python start.py --add-custom-icons
3) make sure the old gecko code to make unuseds selectable is active in dolphin
4) the 6 unused characters will now show the 12 injected images as front and side icons