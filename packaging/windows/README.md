# Windows 安装包构建

在 Windows 构建机上，安装 Visual Studio C++ 桌面工具（x64、ARM64）、Windows SDK 和 .NET Framework，然后在仓库根目录生成设置程序和资源管理器组件：

```powershell
.\windows\build.ps1 -OutputDirectory build-native
```

使用 Python 3 和 NSIS 3 生成当前用户安装程序，输出目录请选新目录：

```powershell
py -3 packaging/windows/build_windows.py --native-dir build-native --output build-windows --makensis "C:\Program Files (x86)\NSIS\makensis.exe"
```

完成原生组件编译后，以上打包步骤也可以在 macOS 或 Linux 上用 `python3` 和 `makensis` 执行。只需验证运行目录时，可加 `--payload-only` 省略 NSIS 编译。

脚本会下载 [CPython 3.13.17 Windows embeddable x64](https://www.python.org/downloads/release/python-31317/) 和 [ExifTool 13.55 Windows x64](https://sourceforge.net/projects/exiftool/files/exiftool-13.55_64.zip/)，比对固定 SHA-256 后解包。原发行包内的 Python、ExifTool 和 Strawberry Perl 许可证随安装文件保留。

安装路径为 `%LOCALAPPDATA%\Programs\ClearJPGHDR`。安装与卸载均使用当前用户权限，分别同步调用主程序的 `--register` 和 `--unregister`。卸载只清理发行包内的文件及本程序的快捷方式和卸载项。
