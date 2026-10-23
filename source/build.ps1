param([string]$InnoCompiler = 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败' }
python -m unittest test_system test_enhanced test_analytics test_desktop test_access test_photos test_ui_cursor test_crud test_password_field test_table_style test_share test_owner -v
if ($LASTEXITCODE -ne 0) { throw '业务测试失败' }
# Keep unrelated native tools' DLLs out of PyInstaller's dependency search.
$buildPython = (Get-Command python).Source
$env:PATH = ((Split-Path $buildPython) + ';' + $env:WINDIR + '\System32;' + $env:WINDIR)
python -m PyInstaller --noconfirm --clean --distpath ..\portable\2.5.0 --workpath build JinfenAssets.spec
if ($LASTEXITCODE -ne 0) { throw 'EXE 打包失败' }
if (-not (Test-Path -LiteralPath $InnoCompiler)) { throw '请传入 Inno Setup ISCC.exe 的完整路径' }
& $InnoCompiler installer.iss
if ($LASTEXITCODE -ne 0) { throw '安装包编译失败' }

