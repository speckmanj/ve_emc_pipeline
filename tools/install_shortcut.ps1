# Make "Valence EMC Scanner" shortcuts (Start Menu and Desktop) for this repo folder.
# The shortcut carries the same AppUserModelID the app sets (emcscan\app.py APP_ID), so
# the running window lands on its own taskbar button and a pin launches this app.
# Run by "Install EMC Scanner.bat"; safe to run again (overwrites the shortcuts).
param(
    [string]$Repo = (Split-Path -Parent $PSScriptRoot),
    [switch]$NoDesktop
)
$ErrorActionPreference = 'Stop'
$AppId = 'Valence.EMCScanner'
$Name  = 'Valence EMC Scanner'
$Repo  = (Resolve-Path $Repo).Path
$Pyw   = Join-Path $Repo '.venv\Scripts\pythonw.exe'
$Icon  = Join-Path $Repo 'emcscan\assets\emc_scanner.ico'
if (-not (Test-Path $Pyw))  { throw "Missing $Pyw. Run 'Install EMC Scanner.bat' first." }
if (-not (Test-Path $Icon)) { throw "Missing $Icon." }

Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class LnkAppId {
    [ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyStore {
        void GetCount(out uint c);
        void GetAt(uint i, out PropertyKey k);
        void GetValue(ref PropertyKey k, IntPtr v);
        void SetValue(ref PropertyKey k, ref PropVariant v);
        void Commit();
    }
    [StructLayout(LayoutKind.Sequential, Pack = 4)]
    struct PropertyKey { public Guid fmtid; public uint pid; }
    [StructLayout(LayoutKind.Explicit, Size = 24)]
    struct PropVariant { [FieldOffset(0)] public ushort vt; [FieldOffset(8)] public IntPtr p; }
    [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
    static extern void SHGetPropertyStoreFromParsingName(string path, IntPtr pbc, int flags,
        ref Guid riid, [MarshalAs(UnmanagedType.Interface)] out IPropertyStore store);

    public static void Set(string lnk, string appId) {
        Guid iid = new Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99");
        IPropertyStore st;
        SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2 /*GPS_READWRITE*/, ref iid, out st);
        PropertyKey key = new PropertyKey();
        key.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"); key.pid = 5; // System.AppUserModel.ID
        PropVariant v = new PropVariant();
        v.vt = 31; /* VT_LPWSTR */ v.p = Marshal.StringToCoTaskMemUni(appId);
        try { st.SetValue(ref key, ref v); st.Commit(); }
        finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(st); }
    }
}
'@

$targets = @([Environment]::GetFolderPath('Programs'))
if (-not $NoDesktop) { $targets += [Environment]::GetFolderPath('Desktop') }
$wsh = New-Object -ComObject WScript.Shell
foreach ($dir in $targets) {
    $lnk = Join-Path $dir "$Name.lnk"
    $s = $wsh.CreateShortcut($lnk)
    $s.TargetPath       = $Pyw
    $s.Arguments        = '-m emcscan app'
    $s.WorkingDirectory = $Repo
    $s.IconLocation     = "$Icon,0"
    $s.Description      = 'Valence near-field EMC scanner'
    $s.Save()
    [LnkAppId]::Set($lnk, $AppId)
    $item = (New-Object -ComObject Shell.Application).Namespace($dir).ParseName("$Name.lnk")
    $got = $item.ExtendedProperty('System.AppUserModel.ID')
    if ($got -ne $AppId) { throw "AppUserModelID not set on $lnk (got '$got')" }
    Write-Host "OK  $lnk"
}
