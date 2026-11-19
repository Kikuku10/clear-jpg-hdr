param([Parameter(Mandatory = $true)][string]$OutputDirectory)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot
$out = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($out) | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (!(Test-Path $compiler)) {
    $compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
$options = @('/nologo','/target:winexe','/platform:anycpu','/optimize+','/codepage:65001',
    "/out:$out\ClearJPGHDR.exe","/win32icon:$root\packaging\windows\AppIcon.ico",
    "/win32manifest:$PSScriptRoot\shell\app.manifest",'/reference:System.Windows.Forms.dll',
    '/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll',
    "$PSScriptRoot\App.cs","$PSScriptRoot\ExplorerMenu.cs")
& $compiler @options
if ($LASTEXITCODE -ne 0) { throw 'Settings app compilation failed.' }
# Explorer loads a DLL of its own architecture, including on Windows on Arm.
foreach ($target in @('x64', 'arm64')) {
    & "$PSScriptRoot\shell\build.ps1" -OutputDirectory $out -Architecture $target
    if (!$?) { throw "Could not build Explorer extension ($target)." }
}
