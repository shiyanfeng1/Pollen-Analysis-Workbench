$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$releaseVersion = ((Get-Content -LiteralPath 'pollen\__init__.py' -Raw) -split "'")[1]
python tools/build_assets.py
if ($LASTEXITCODE -ne 0) { throw '帮助与图标资源生成失败' }
python -m PyInstaller --noconfirm --windowed --name PollenStudio --distpath "dist/v$releaseVersion" --workpath "build/v${releaseVersion}_package" --paths .deps --add-data '.deps/PySide6/translations/qtbase_zh_CN.qm:PySide6/translations' --add-data 'assets/pollen.ico:assets' --icon assets/pollen.ico --version-file assets/windows-version.txt --hidden-import matplotlib.backends.backend_pdf --hidden-import matplotlib.backends.backend_svg --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets --exclude-module PySide6.QtPdf --exclude-module scipy --exclude-module pandas --exclude-module tkinter --exclude-module IPython --exclude-module pytest --exclude-module matplotlib.tests main.py
if ($LASTEXITCODE -ne 0) { throw 'Windows 打包失败' }
# Qt 6.12 uses Windows' unversioned ICU API. Conda's versioned ICU DLL
# shadows it when PyInstaller collects dependencies, so omit that copy.
$releaseTarget = Join-Path $PSScriptRoot "dist\v$releaseVersion\PollenStudio"
$icuTarget = Join-Path $releaseTarget '_internal\icuuc.dll'
if (Test-Path -LiteralPath $icuTarget) { Remove-Item -LiteralPath $icuTarget }
Copy-Item -Path '.deps/PySide6/msvcp140*.dll','.deps/PySide6/vcruntime140*.dll' -Destination (Join-Path $releaseTarget '_internal') -Force
python release.py --output $releaseTarget
if ($LASTEXITCODE -ne 0) { throw '示例文件生成失败' }
