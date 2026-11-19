param(
    [Parameter(Mandatory=$true)][string]$OutputDirectory,
    [Parameter(Mandatory=$true)][ValidateSet('x64','arm64')][string]$Architecture
)
$ErrorActionPreference = 'Stop'
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vs = & $vswhere -latest -products '*' -property installationPath
if (!$vs) { throw 'Visual Studio with the C++ Desktop tools is required.' }
$tools = Get-ChildItem "$vs\VC\Tools\MSVC" -Directory | Sort-Object Name -Descending | Select-Object -First 1
$nativeCpu = (Get-CimInstance Win32_Processor | Select-Object -First 1).Architecture
$allowedHosts = if ($nativeCpu -eq 12) { @('arm64','x64','x86') } else { @('x64','x86') }
$hostCpu = $allowedHosts | Where-Object { Test-Path "$($tools.FullName)\bin\Host$_\$Architecture\cl.exe" } | Select-Object -First 1
if (!$hostCpu) { throw "C++ tools for target $Architecture were not found." }
$stage = [IO.Path]::GetFullPath($OutputDirectory)
$manifestDir = "$stage\modern\$Architecture"
New-Item -ItemType Directory -Force $manifestDir | Out-Null
$work = New-Item -ItemType Directory (Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString()))
try {
    Push-Location $work.FullName
    try {
        $cmd = 'call "{0}\Common7\Tools\VsDevCmd.bat" -no_logo -host_arch={1} -arch={2} && cl /nologo /EHsc /O2 /MT /std:c++17 /utf-8 /LD "{3}\ExplorerCommand.cpp" /link /OUT:"{4}\ExplorerMenu-{2}.dll" /EXPORT:DllGetClassObject /EXPORT:DllCanUnloadNow /IMPLIB:ExplorerMenu.lib ole32.lib shell32.lib shlwapi.lib advapi32.lib uuid.lib' -f $vs,$hostCpu,$Architecture,$PSScriptRoot,$stage
        & $env:ComSpec /d /c $cmd
        if ($LASTEXITCODE) { throw "C++ shell build failed ($LASTEXITCODE)." }
    } finally { Pop-Location }

    $manifest = [IO.File]::ReadAllText("$PSScriptRoot\AppxManifest.template.xml").Replace('@ARCH@',$Architecture)
    [IO.File]::WriteAllText("$manifestDir\AppxManifest.xml",$manifest)
    Copy-Item "$PSScriptRoot\Assets" -Destination $stage -Recurse -Force
    # Sparse packages reference binaries in the external install directory.
    # A developer-mode registration uses the loose manifest directly.
    $sdk = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin" -Directory | Sort-Object Name -Descending
    $makeappx = $sdk | ForEach-Object { Join-Path $_.FullName 'x64\makeappx.exe' } | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (!$makeappx) { throw 'Windows SDK MakeAppx.exe is required.' }
    $source = New-Item -ItemType Directory "$($work.FullName)\manifest"
    Copy-Item "$manifestDir\AppxManifest.xml" $source.FullName
    & $makeappx pack /o /nv /d $source.FullName /p "$manifestDir\identity.msix"
    if ($LASTEXITCODE) { throw "Sparse MSIX packaging failed ($LASTEXITCODE)." }
} finally { Remove-Item -LiteralPath $work.FullName -Force -Recurse }
