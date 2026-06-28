@echo off
echo Sluggers Dat Tools
echo ==================
echo [1] Extract all models, icons ^& 'untangle' textures (for Dolphin texture loader)
echo [2] Extract all models - no texture untangling
echo [3] Extract player icons only
echo [4] Patch model into game files
echo [5] UnPatch model in game files
echo [6] Create or resize hammerspace (extra model data storage)
echo [7] Reimport patched icon sheets into dt_na.dat
echo.
set /p "choice=Enter option: "
echo.

if "%choice%"=="1" (
    python start.py --export-icons
    if errorlevel 1 goto end
    python start.py --export --untangle
    goto end
)
if "%choice%"=="2" (
    python start.py --export
    goto end
)
if "%choice%"=="3" (
    python start.py --export-icons
    goto end
)
if "%choice%"=="4" (
    set /p "files=Enter file name^(s^): "
    python start.py --patch %files%
    goto end
)
if "%choice%"=="5" (
    set /p "files=Enter file name^(s^): "
    python start.py --unpatch %files%
    goto end
)
if "%choice%"=="6" (
    python start.py -hs
    goto end
)
if "%choice%"=="7" (
    echo Shared image handling:
    echo   [1] strict (fail on conflicting shared-image edits)
    echo   [2] first-page (force lowest texture index per shared group)
    set /p "smode=Choose mode [1/2]: "
    if "%smode%"=="2" (
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

