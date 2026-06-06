@echo off
echo Sluggers Dat Tools
echo ==================
echo [1] Extract all models ^& 'untangle' textures (for Dolphin texture loader)
echo [2] Extract all models - no untangling
echo [3] Patch model into game files
echo [4] UnPatch model in game files
echo [5] Create or resize hammerspace (extra model data storage)
echo.
set /p "choice=Enter option: "
echo.

if "%choice%"=="1" (
    python start.py --export --untangle
    goto end
)
if "%choice%"=="2" (
    python start.py --export
    goto end
)
if "%choice%"=="3" (
    set /p "files=Enter file name^(s^): "
    python start.py --patch %files%
    goto end
)
if "%choice%"=="4" (
    set /p "files=Enter file name^(s^): "
    python start.py --unpatch %files%
    goto end
)
if "%choice%"=="5" (
    python start.py -hs
    goto end
)

echo Invalid option. Exiting.

:end
echo.
pause

