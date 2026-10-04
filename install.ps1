# QiQiKou (breath remover) Windows installer
# Usage (PowerShell):  irm https://raw.githubusercontent.com/lilanqiao/qiqikou-tool/main/install.ps1 | iex
# Only downloads the Windows build, installs to %LOCALAPPDATA%\Programs (no admin needed),
# and creates Desktop + Start Menu shortcuts.
# NOTE: this file is generated from tools/install.ps1.template (run tools/gen_install_ps1.py); non-ASCII text is \u-escaped
#       so it survives `irm | iex` on Windows PowerShell 5.1.

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function U($s) { [regex]::Unescape($s) }

# Shortcuts via IShellLinkW (Unicode). WScript.Shell's shortcut object rejects paths with
# characters outside the system ANSI code page (e.g. Chinese names on English Windows).
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class QKLink {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")] class CShellLink {}
    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown), Guid("000214F9-0000-0000-C000-000000000046")]
    interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder f, int c, IntPtr d, int fl);
        void GetIDList(out IntPtr p); void SetIDList(IntPtr p);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder n, int c);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string n);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder d, int c);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string d);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder a, int c);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string a);
        void GetHotkey(out short k); void SetHotkey(short k);
        void GetShowCmd(out int s); void SetShowCmd(int s);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder p, int c, out int i);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string p, int i);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string p, int r);
        void Resolve(IntPtr h, int f);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string f);
    }
    public static void Create(string lnk, string target, string workdir) {
        var l = (IShellLinkW)new CShellLink();
        l.SetPath(target); l.SetWorkingDirectory(workdir); l.SetIconLocation(target, 0);
        ((IPersistFile)l).Save(lnk, true);
    }
}
'@

$Repo    = 'lilanqiao/qiqikou-tool'
$AppName = (U '\u53bb\u6c14\u53e3\u5de5\u5177')
$Dest    = Join-Path $env:LOCALAPPDATA "Programs\QiQiKou"
if ($env:QIQIKOU_DEST) { $Dest = $env:QIQIKOU_DEST }          # for testing only

if (-not [Environment]::Is64BitOperatingSystem) {
    Write-Host (U '\u53ea\u652f\u6301 64 \u4f4d Windows 10/11') -ForegroundColor Red; return
}

Write-Host ""
Write-Host (U '== \u53bb\u6c14\u53e3\u5de5\u5177 \u5b89\u88c5 ==') -ForegroundColor Yellow
Write-Host (U '\u68c0\u6d4b\u5230\u7cfb\u7edf\uff1aWindows\uff0864 \u4f4d\uff09\uff0c\u53ea\u4e0b\u8f7d Windows \u7248')

$rel   = Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest" -Headers @{ 'User-Agent' = 'qiqikou-installer' }
$asset = $rel.assets | Where-Object { $_.name -like 'QiQiKou-Windows-*.zip' } | Select-Object -First 1
if (-not $asset) { Write-Host (U '\u53d1\u5e03\u9875\u4e0a\u6ca1\u627e\u5230 Windows \u5b89\u88c5\u5305') -ForegroundColor Red; return }
Write-Host ((U (U '\u4e0b\u8f7d\uff1a{0}\uff08{1} MB\uff09')) -f $asset.name, [math]::Round($asset.size / 1MB))

$tmp = Join-Path $env:TEMP ("qiqikou_" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    $zip = Join-Path $tmp 'app.zip'
    Invoke-WebRequest $asset.browser_download_url -OutFile $zip -UseBasicParsing
    Write-Host (U '\u89e3\u538b\u4e2d\u2026')
    Expand-Archive $zip -DestinationPath $tmp -Force
    $src = Get-ChildItem $tmp -Directory | Where-Object { Test-Path (Join-Path $_.FullName '*.exe') } | Select-Object -First 1
    if (-not $src) { throw 'zip layout unexpected' }

    # Close a running copy first, otherwise its files are locked
    Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -and $_.Path.StartsWith($Dest) } | Stop-Process -Force
    Start-Sleep -Milliseconds 500
    if (Test-Path $Dest) { Remove-Item $Dest -Recurse -Force }
    New-Item -ItemType Directory -Path (Split-Path $Dest) -Force | Out-Null
    Move-Item $src.FullName $Dest
    Get-ChildItem $Dest -Recurse -File | Unblock-File

    $exe = Join-Path $Dest ($AppName + '.exe')
    $desk = [Environment]::GetFolderPath('Desktop')
    if (-not $desk) { $desk = Join-Path $env:USERPROFILE 'Desktop' }
    $menu = [Environment]::GetFolderPath('Programs')
    if ($env:QIQIKOU_LINKDIR) { $desk = $menu = $env:QIQIKOU_LINKDIR }   # for testing only
    $links = @((Join-Path $desk ($AppName + '.lnk')), (Join-Path $menu ($AppName + '.lnk'))) | Select-Object -Unique
    foreach ($l in $links) { [QKLink]::Create($l, $exe, $Dest) }
    Write-Host ""
    Write-Host (U '\u5b89\u88c5\u5b8c\u6210\uff01\u684c\u9762\u548c\u5f00\u59cb\u83dc\u5355\u90fd\u6709\u300c\u53bb\u6c14\u53e3\u5de5\u5177\u300d\uff0c\u53cc\u51fb\u5373\u53ef\u4f7f\u7528') -ForegroundColor Green
    Write-Host ((U (U '\u5b89\u88c5\u4f4d\u7f6e\uff1a{0}')) -f $Dest)
}
catch {
    Write-Host ""
    Write-Host (U '\u5b89\u88c5\u5931\u8d25\uff1a') -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage
    throw
}
finally {
    Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
}
