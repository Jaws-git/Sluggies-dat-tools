@echo off
setlocal
rem Copies the patched game files (dt_na.dat, main.dol, fst.bin) from this
rem folder into the unpacked game. The target game directory is stored in the
rem "sluggiespath" file next to this script; on first run it is asked for.
rem
rem Exit code 0: every file found here was copied. Exit code 1: nothing or not
rem everything was copied; the reason is printed.
rem
rem Run from the Sluggies Tools GUI (SLUGGIES_GUI_PROMPTS is set) the script
rem never waits for input: the GUI asks for the game directory itself and
rem writes "sluggiespath" before it starts the script.

set "src_dir=%~dp0"
set "pathfile=%src_dir%sluggiespath"
set "dir="
set "new_dir="
set /a copied=0
set /a failed=0

if exist "%pathfile%" set /p dir=<"%pathfile%"
if defined dir goto clean_dir

if defined SLUGGIES_GUI_PROMPTS goto no_stored_dir
echo Please enter the path to your unpacked copy of Mario Super Sluggers (US) main folder:
echo (this only has to be done once)
set /p "dir="
if not defined dir goto no_path_entered
set "new_dir=1"

:clean_dir
rem no quotes, no trailing blank (an older "echo %%dir%%>file" left one), no trailing backslash
set "dir=%dir:"=%"
if not defined dir goto no_path_entered
if "%dir:~-1%"==" " set "dir=%dir:~0,-1%"
if "%dir:~-1%"=="\" set "dir=%dir:~0,-1%"

if not exist "%dir%\" goto bad_dir
if not exist "%dir%\DATA\files\" goto not_game_dir
if not exist "%dir%\DATA\sys\" goto not_game_dir

rem (no parenthesised blocks around paths: a ")" in a folder name would end them)
if not defined new_dir goto copy_files
>"%pathfile%" echo %dir%
if errorlevel 1 echo WARNING: Could not save the path to "%pathfile%"; it will be asked for again next time.

:copy_files
call :copy_one dt_na.dat DATA\files\dt_na.dat
call :copy_one main.dol DATA\sys\main.dol
call :copy_one fst.bin DATA\sys\fst.bin

if %failed% GTR 0 goto copy_failed
if %copied% EQU 0 goto nothing_to_copy
echo Done: %copied% file(s) copied to "%dir%".
exit /b 0


:copy_one
rem %1: the file here, %2: its place in the game directory
if not exist "%src_dir%%~1" goto :eof
copy /Y "%src_dir%%~1" "%dir%\%~2" >nul
if errorlevel 1 goto copy_one_failed
echo Copied %~1 to "%dir%\%~2"
set /a copied+=1
goto :eof
:copy_one_failed
echo ERROR: Could not copy %~1 to "%dir%\%~2".
echo        Is the game or Dolphin still running, or is the file read-only?
set /a failed+=1
goto :eof


:no_stored_dir
echo ERROR: No game directory is stored in "%pathfile%". Nothing was copied.
goto fail

:no_path_entered
echo ERROR: No path entered. Nothing was copied.
goto fail

:bad_dir
echo ERROR: The game directory "%dir%" does not exist. Nothing was copied.
goto fix_hint

:not_game_dir
echo ERROR: "%dir%" has no DATA\files and DATA\sys folders: it is not the main folder
echo        of an unpacked Mario Super Sluggers. Nothing was copied.
goto fix_hint

:fix_hint
if defined new_dir goto fail
echo        Correct or delete the "sluggiespath" file in this folder and try again.
goto fail

:nothing_to_copy
echo ERROR: None of dt_na.dat, main.dol or fst.bin is in "%src_dir%". Nothing was copied.
goto fail

:copy_failed
echo ERROR: %failed% file(s) could not be copied (%copied% copied). The game directory is incomplete.
goto fail

:fail
rem a double-clicked script keeps its window open; under the GUI nothing waits for input
if not defined SLUGGIES_GUI_PROMPTS pause
exit /b 1
