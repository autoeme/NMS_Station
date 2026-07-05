@echo off
rem Сборка NMS_Station.exe (нужен: pip install pyinstaller pillow zstandard)
python -m PyInstaller --noconfirm --clean --onefile --windowed --name NMS_Station ^
  --hidden-import nms_indexer --hidden-import nms_lookup --hidden-import zstandard ^
  nms_station.py
echo.
echo Готово: dist\NMS_Station.exe  (рядом с ним должен лежать MBINCompiler.exe)
pause
