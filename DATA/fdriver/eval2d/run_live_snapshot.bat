@echo off
rem fdriver 2d live snapshot (prereg v2). Called by Windows Task Scheduler (task: fdriver_2d_live_snapshot).
rem Quarter = last completed quarter at run time. Extra args pass through (e.g. --dry).
rem Log: OneDrive\INVESTMENT\<kr>\fdriver\2d\live\logs\snapshot_<Q>_<yyyymmdd_hhmmss>.log
chcp 65001 >nul
setlocal
set "ROOT=%~dp0..\..\.."
set "PY=C:\Users\82108\AppData\Local\Programs\Python\Python39\python.exe"
set "PYTHONPATH=%ROOT%"
set "PYTHONIOENCODING=utf-8"
cd /d "%ROOT%"
for /f "usebackq tokens=1,2,3" %%A in (`%PY% -c "import datetime as d;from DATA.fdriver.config import output_path as o;t=d.datetime.now();q=(t.month-1)//3;y=t.year-(q==0);q=q or 4;print(f'{y}Q{q}',t.strftime('%%Y%%m%%d_%%H%%M%%S'),o('2d','live','logs'))"`) do (
    set "Q=%%A"
    set "TS=%%B"
    set "LOGDIR=%%C"
)
if not defined Q (
    echo %date% %time% quarter/log setup failed >> "%~dp0run_live_snapshot_error.log"
    exit /b 2
)
set "LOG=%LOGDIR%\snapshot_%Q%_%TS%.log"
echo start %TS% quarter %Q% args %* > "%LOG%"
%PY% -W ignore -m DATA.fdriver.eval2d.fd_live_snapshot --quarter %Q% %* >> "%LOG%" 2>&1
set "RC=%ERRORLEVEL%"
echo exit %RC% >> "%LOG%"
exit /b %RC%
