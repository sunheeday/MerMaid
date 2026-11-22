# docs/proposal/ui/src/*.html -> docs/proposal/ui/MerMaid_<name>_<version>.pdf (headless Chrome print)
# Version is read from each HTML <title> (e.g. "... v0.2").
# Run: powershell -ExecutionPolicy Bypass -File docs/proposal/ui/build_pdf.ps1
# (ASCII only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI)
$ErrorActionPreference = 'Stop'
$chrome = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
            "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $chrome) { throw 'Chrome or Edge not found' }
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Get-ChildItem (Join-Path $root 'src') -Filter *.html | ForEach-Object {
  $html = [IO.File]::ReadAllText($_.FullName, [Text.Encoding]::UTF8)
  $m = [regex]::Match($html, '<title>[^<]*?(v\d+\.\d+)</title>')
  $ver = if ($m.Success) { $m.Groups[1].Value } else { 'v0.1' }
  $out = Join-Path $root ("MerMaid_" + $_.BaseName + "_" + $ver + ".pdf")
  $url = ([System.Uri]$_.FullName).AbsoluteUri
  $p = Start-Process -FilePath $chrome -ArgumentList @('--headless=new', '--disable-gpu', '--no-pdf-header-footer',
        '--virtual-time-budget=3000', "--print-to-pdf=`"$out`"", $url) -PassThru -Wait -WindowStyle Hidden
  if (Test-Path $out) { Write-Host "OK   $out" } else { Write-Host "FAIL $($_.Name) (exit $($p.ExitCode))" }
}
