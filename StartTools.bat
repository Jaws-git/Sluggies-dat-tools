@echo off
setlocal EnableDelayedExpansion
echo Sluggers Dat Tools
echo ==================
echo [1] Extract all models, icons ^& 'untangle' textures (for Dolphin texture loader)
echo [2] Extract all models - no texture untangling
echo [3] Extract player icons only
echo ---
echo [4] Patch all 6 unused-character icons into game files
echo ---
echo [5] Patch .sluggies model into game files
echo [6] UnPatch .sluggies model in game files
echo [7] Manually resize available hammerspace (extra model data storage) - usually not necessary
echo [8] Reimport patched standard icon sheets into dt_na.dat
echo.
set "tools_choice="
set /p "tools_choice=Enter option: "
echo.

if "!tools_choice!"=="1" (
    python start.py --export --untangle
    if errorlevel 1 goto end
    python start.py --prepare-icon-routes --no-overwrite-copy
    if errorlevel 1 goto end
    python start.py --export-icons --use-output
    goto end
)
if "!tools_choice!"=="2" (
    python start.py --export
    goto end
)
if "!tools_choice!"=="3" (
    python start.py --export-icons
    goto end
)
if "!tools_choice!"=="4" (
    call python start.py --add-custom-icons
    set "tools_result=!errorlevel!"
    if not "!tools_result!"=="0" (
        echo.
        echo Custom icon patching failed with exit code !tools_result!.
    ) else (
        echo.
        echo Custom icon patching completed successfully.
    )
    goto end
)
if "!tools_choice!"=="5" (
    set "model_files="
    set /p "model_files=Enter file name(s): "
    python start.py --patch !model_files!
    goto end
)
if "!tools_choice!"=="6" (
    set "model_files="
    set /p "model_files=Enter file name(s): "
    python start.py --unpatch !model_files!
    goto end
)
if "!tools_choice!"=="7" (
    python start.py -hs
    goto end
)
if "!tools_choice!"=="8" (
    echo Shared image handling:
    echo   [1] strict ^(fail on conflicting shared-image edits^)
    echo   [2] first-page ^(force lowest texture index per shared group^)
    set "icon_shared_mode="
    set /p "icon_shared_mode=Choose mode [1/2]: "
    if "!icon_shared_mode!"=="2" (
        python start.py --patch-icons --shared-mode first-page
    ) else (
        python start.py --patch-icons
    )
    goto end
)

echo Invalid option. Exiting.

:end
echo.
pause
goto :eof

