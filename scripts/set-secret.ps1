# Store an API key for Barnaby in Windows Credential Manager (never in a file or in chat).
#
#   Copy the key, then:   powershell -ExecutionPolicy Bypass -File .\scripts\set-secret.ps1 devin
#
# Reads the key from the clipboard (or a hidden prompt if the clipboard doesn't hold one),
# writes it with CredWrite directly (cmdkey mis-parses keys containing "/"), then clears
# the clipboard. The engine reads it back as generic credential "Barnaby-<name>".
param([ValidateSet('devin', 'openai')][string]$Name = 'devin')
$ErrorActionPreference = 'Stop'

Add-Type -Namespace BarnabySecrets -Name Native -MemberDefinition @'
[StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
public struct CREDENTIAL {
    public int Flags; public int Type; public string TargetName; public string Comment;
    public long LastWritten; public int CredentialBlobSize; public IntPtr CredentialBlob;
    public int Persist; public int AttributeCount; public IntPtr Attributes;
    public string TargetAlias; public string UserName;
}
[DllImport("advapi32.dll", EntryPoint = "CredWriteW", CharSet = CharSet.Unicode, SetLastError = true)]
public static extern bool CredWrite(ref CREDENTIAL credential, int flags);
'@

$key = (Get-Clipboard -Raw)
if ($key) { $key = $key.Trim() }
if (-not $key -or $key -match '\s' -or $key.Length -lt 16) {
    $secure = Read-Host "Clipboard has no key. Paste the $Name key (right-click pastes; input is hidden)" -AsSecureString
    $key = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure)).Trim()
}
if (-not $key -or $key -match '\s' -or $key.Length -lt 16) { throw "That doesn't look like an API key; nothing was saved." }

$blob = [Runtime.InteropServices.Marshal]::StringToCoTaskMemUni($key)
try {
    $cred = New-Object BarnabySecrets.Native+CREDENTIAL
    $cred.Type = 1                      # CRED_TYPE_GENERIC
    $cred.TargetName = "Barnaby-$Name"
    $cred.UserName = $Name
    $cred.Persist = 2                   # CRED_PERSIST_LOCAL_MACHINE: this user, this PC
    $cred.CredentialBlob = $blob
    $cred.CredentialBlobSize = $key.Length * 2
    if (-not [BarnabySecrets.Native]::CredWrite([ref]$cred, 0)) {
        throw "CredWrite failed (Win32 error $([Runtime.InteropServices.Marshal]::GetLastWin32Error()))"
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeCoTaskMemUnicode($blob)
    Set-Clipboard -Value ' '
    Remove-Variable key -ErrorAction SilentlyContinue
}
Write-Host "Saved Barnaby-$Name ($($cred.CredentialBlobSize / 2) characters). Clipboard cleared." -ForegroundColor Green
