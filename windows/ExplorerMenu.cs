using System;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32;

namespace ClearJPGHDR {
    internal static class ExplorerMenu {
        private const string State = @"Software\ClearJPGHDR\Explorer";
        private const string Classes = @"Software\Classes\";
        private const string Clsid = "{7FDE7152-B042-41B3-B928-54E52A324C91}";
        private const string ComKey = Classes + @"CLSID\" + Clsid;
        private const string Verb = @"\shell\ClearJPGHDR.RemoveGainMap";
        private static readonly string[] Types = { @"SystemFileAssociations\.jpg", @"SystemFileAssociations\.jpeg", "Directory", @"Directory\Background" };
        private static string Root { get { return AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar); } }
        [StructLayout(LayoutKind.Sequential)]
        private struct SystemInfo {
            public ushort Arch, Reserved; public uint PageSize;
            public IntPtr MinAddress, MaxAddress, CpuMask;
            public uint CpuCount, Type, AllocationGranularity; public ushort Level, Revision;
        }
        [DllImport("kernel32.dll")] private static extern void GetNativeSystemInfo(out SystemInfo info);
        [DllImport("kernel32.dll")] private static extern IntPtr GetCurrentProcess();
        [DllImport("kernel32.dll", SetLastError=true)] private static extern bool IsWow64Process2(IntPtr process, out ushort processMachine, out ushort nativeMachine);
        [DllImport("shell32.dll")] private static extern void SHChangeNotify(int change, uint flags, IntPtr item1, IntPtr item2);
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode)]
        private static extern int GetPackagesByPackageFamily(string family, ref uint count, IntPtr names, ref uint chars, IntPtr buffer);
        private static string NativeArch() {
            // Under x64 emulation on ARM64, GetNativeSystemInfo reports AMD64.
            try {
                ushort processMachine, nativeMachine;
                if (IsWow64Process2(GetCurrentProcess(), out processMachine, out nativeMachine)) {
                    if (nativeMachine == 0xAA64) return "arm64";
                    if (nativeMachine == 0x8664) return "x64";
                }
            } catch (EntryPointNotFoundException) { /* earlier Windows 10 */ }
            SystemInfo info; GetNativeSystemInfo(out info);
            if (info.Arch == 12) return "arm64";
            if (info.Arch == 9) return "x64";
            throw new PlatformNotSupportedException("需要 64 位 Windows。");
        }
        private static string DllPath() { return Path.Combine(Root, "ExplorerMenu-" + NativeArch() + ".dll"); }
        private static RegistryKey HKCU() { return RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64); }
        private static bool SamePath(object path, string other) {
            return String.Equals(path as string, other, StringComparison.OrdinalIgnoreCase);
        }
        public static bool IsEnabled() {
            using (var user = HKCU())
            using (var state = user.OpenSubKey(State))
            using (var dll = user.OpenSubKey(ComKey + @"\InprocServer32")) {
                return state != null && (int)state.GetValue("Enabled", 0) == 1 &&
                       dll != null && SamePath(dll.GetValue(null), DllPath());
            }
        }
        private static bool PackagePresent(string family) {
            if (String.IsNullOrEmpty(family)) return false;
            uint count = 0, chars = 0;
            int result = GetPackagesByPackageFamily(family, ref count, IntPtr.Zero, ref chars, IntPtr.Zero);
            return (result == 0 || result == 122) && count > 0;
        }
        public static bool IsModernEnabled() {
            if (!IsEnabled()) return false;
            using (var user = HKCU())
            using (var state = user.OpenSubKey(State)) {
                return state != null && SamePath(state.GetValue("PackageRoot"), Root)
                       && PackagePresent(state.GetValue("PackageFamily") as string);
            }
        }
        // Inputs go through the child environment; the encoded PowerShell script is fixed.
        private static string PackageCommand(string script, string identity) {
            var start = new ProcessStartInfo(Path.Combine(Environment.SystemDirectory,
                @"WindowsPowerShell\v1.0\powershell.exe"), "-NoLogo -NoProfile -NonInteractive -EncodedCommand " +
                Convert.ToBase64String(Encoding.Unicode.GetBytes("$ErrorActionPreference='Stop';" + script)));
            start.UseShellExecute = false; start.CreateNoWindow = true;
            start.RedirectStandardOutput = true; start.RedirectStandardError = true;
            start.EnvironmentVariables["CLEARHDR_APPDIR"] = Root;
            start.EnvironmentVariables["CLEARHDR_IDENTITY"] = identity;
            using (var p = Process.Start(start)) {
                var output = p.StandardOutput.ReadToEndAsync();
                var error = p.StandardError.ReadToEndAsync();
                if (!p.WaitForExit(60000)) { p.Kill(); return ""; }
                System.Threading.Tasks.Task.WaitAll(output, error);
                string text = output.Result.Trim();
                return p.ExitCode == 0 ? text : "";
            }
        }
        private static void TryRegisterModern(RegistryKey state) {
            if (Environment.OSVersion.Version.Build < 22000) return;
            if (SamePath(state.GetValue("PackageRoot"), Root) && PackagePresent(state.GetValue("PackageFamily") as string)) return;
            string dir = Path.Combine(Root, "modern", NativeArch());
            string signed = Path.Combine(dir, "identity.signed.msix");
            string manifest = Path.Combine(dir, "AppxManifest.xml");
            string command, identity;
            if (File.Exists(signed)) {
                command = "Add-AppxPackage -Path $env:CLEARHDR_IDENTITY -ExternalLocation $env:CLEARHDR_APPDIR;";
                identity = signed;
            } else {
                using (var machine = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry64))
                using (var dev = machine.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock")) {
                    if (dev == null || (int)dev.GetValue("AllowDevelopmentWithoutDevLicense", 0) != 1 || !File.Exists(manifest)) return;
                }
                command = "Add-AppxPackage -Register $env:CLEARHDR_IDENTITY -ExternalLocation $env:CLEARHDR_APPDIR;";
                identity = manifest;
            }
            string family = PackageCommand(command + "(Get-AppxPackage -Name Kikuku10.ClearJPGHDR | Where-Object { $_.Publisher -eq 'CN=Kikuku10' }).PackageFamilyName", identity);
            if (family.StartsWith("Kikuku10.ClearJPGHDR_", StringComparison.OrdinalIgnoreCase) && PackagePresent(family)) {
                state.SetValue("PackageRoot", Root);
                state.SetValue("PackageFamily", family);
            }
        }
        public static void SetEnabled(bool enabled) {
            using (var user = HKCU())
            using (var state = user.CreateSubKey(State)) {
                string dll = DllPath();
                if (enabled) {
                    if (!File.Exists(dll)) throw new FileNotFoundException("找不到资源管理器扩展。", dll);
                    using (var key = user.CreateSubKey(ComKey + @"\InprocServer32")) {
                        key.SetValue(null, dll);
                        key.SetValue("ThreadingModel", "Apartment");
                    }
                    foreach (var type in Types) {
                        using (var key = user.CreateSubKey(Classes + type + Verb)) {
                            key.SetValue("MUIVerb", "清除 JPG HDR 增益图");
                            key.SetValue("ExplorerCommandHandler", Clsid);
                            key.SetValue("MultiSelectModel", "Player");
                            key.SetValue("Icon", "\"" + Path.Combine(Root, "ClearJPGHDR.exe") + "\",0");
                        }
                    }
                    state.SetValue("Enabled", 1, RegistryValueKind.DWord);
                    TryRegisterModern(state);
                } else {
                    // A previous portable copy may have yielded these registrations to a newer install.
                    using (var key = user.OpenSubKey(ComKey + @"\InprocServer32")) {
                        if (key != null && !SamePath(key.GetValue(null), dll)) return;
                    }
                    state.SetValue("Enabled", 0, RegistryValueKind.DWord);
                    foreach (var type in Types) user.DeleteSubKeyTree(Classes + type + Verb, false);
                    user.DeleteSubKeyTree(ComKey, false);
                }
            }
            SHChangeNotify(0x08000000, 0, IntPtr.Zero, IntPtr.Zero);
        }
        public static void Uninstall() {
            SetEnabled(false);
            using (var user = HKCU())
            using (var state = user.OpenSubKey(State, true)) {
                if (state == null || !SamePath(state.GetValue("PackageRoot"), Root)) return;
                string family = state.GetValue("PackageFamily") as string;
                if (PackagePresent(family)) {
                    PackageCommand("Get-AppxPackage -Name Kikuku10.ClearJPGHDR | Where-Object { $_.Publisher -eq 'CN=Kikuku10' -and $_.PackageFamilyName -eq $env:CLEARHDR_IDENTITY } | Remove-AppxPackage", family);
                    if (PackagePresent(family)) throw new IOException("无法移除已注册的 Windows 右键菜单包。");
                }
                state.DeleteValue("PackageRoot", false); state.DeleteValue("PackageFamily", false);
            }
        }
    }
}
