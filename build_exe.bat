@echo off
echo Building EXE...
pip install pyinstaller
pyinstaller --noconfirm --onefile --windowed --name "MinecraftModTranslator" --add-data "ui/styles.qss;ui" main.py
echo Build complete! Check the dist folder.
pause
