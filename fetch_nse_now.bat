@echo off
REM ============================================================================
REM  fetch_nse_now.bat  —  v17.13
REM  ONE-CLICK MANUAL TRIGGER for the NSE pledge/DII snapshot fetch.
REM
REM  Double-click this any time (e.g. if the laptop was OFF at the scheduled
REM  Sunday time). It runs the EXACT same command Task Scheduler runs, so a
REM  manual run and a scheduled run are guaranteed identical — same fetch,
REM  same validation, same commit/push.
REM
REM  The window stays open at the end so you can read the result.
REM  Exit codes: 0 = ok (or unchanged) · 2 = NSE returned nothing (are you on
REM  a residential IP? try again later) · 1 = git push failed (file written
REM  locally; push by hand)
REM ============================================================================
setlocal
cd /d "%~dp0"

echo.
echo  NSE pledge/DII snapshot — manual run  %date% %time%
echo  ---------------------------------------------------
python fetch_nse_local.py --push
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
    echo  DONE — snapshot fetched and pushed. Next GitHub Actions run will use it.
) else if "%RC%"=="2" (
    echo  NOTHING FETCHED — NSE returned no records from this IP. Previous
    echo  snapshot kept. Check you are on home/office broadband, not a VPN or
    echo  datacenter network, and try again in a few minutes.
) else (
    echo  FETCHED but git push FAILED ^(rc=%RC%^). The file is written locally at
    echo  data\nse_snapshot.json — run:  git add data/nse_snapshot.json ^&^& git commit -m "chore(nse-snapshot): manual" ^&^& git push
)
echo.
pause
endlocal
exit /b %RC%