using Microsoft.Win32;
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace ClearJPGHDR
{
    internal static class App
    {
        internal const string Title = "清除JPG HDR小工具";
        internal const string Preferences = @"HKEY_CURRENT_USER\Software\ClearJPGHDR";

        internal static bool ReadOption(string name, bool fallback)
        {
            return Convert.ToInt32(Registry.GetValue(Preferences, name, fallback ? 1 : 0) ?? (fallback ? 1 : 0)) != 0;
        }

        internal static void WriteOption(string name, bool value)
        {
            Registry.SetValue(Preferences, name, value ? 1 : 0, RegistryValueKind.DWord);
        }

        [STAThread]
        private static int Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            bool installer = args.Length == 1 && (args[0] == "--register" || args[0] == "--unregister");
            try
            {
                if (installer)
                {
                    if (args[0] == "--register") ExplorerMenu.SetEnabled(ReadOption("MenuEnabled", true));
                    else ExplorerMenu.Uninstall();
                    return 0;
                }
                if (args.Length == 0)
                {
                    Application.Run(new SettingsWindow());
                    return 0;
                }
                string[] paths;
                if (args[0] == "--process" && args.Length >= 2)
                    paths = args.Skip(1).ToArray();
                else if (args[0] == "--selection" && args.Length == 2)
                    paths = ReadSelection(args[1]);
                else
                    throw new ArgumentException("请选择要处理的 JPG 图片或文件夹。");
                if (!ReadOption("MenuEnabled", true)) return 0;
                Report report;
                using (var window = new ProgressWindow())
                    report = window.ProcessSelection(paths);
                bool failed = report.failed.Length != 0;
                MessageBox.Show(report.message, report.title, MessageBoxButtons.OK,
                    failed ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
                return failed ? 1 : 0;
            }
            catch (Exception e)
            {
                if (!installer)
                    MessageBox.Show(e.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return 1;
            }
        }

        private static string[] ReadSelection(string request)
        {
            Guid id = Guid.ParseExact(request, "B");
            string path = Path.Combine(Path.GetTempPath(), "ClearJPGHDR-" + id.ToString("B") + ".selection");
            using (var file = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None,
                                             4096, FileOptions.DeleteOnClose))
            using (var reader = new StreamReader(file, Encoding.Unicode))
                return reader.ReadToEnd().Split(new[] { '\0' }, StringSplitOptions.RemoveEmptyEntries);
        }

        private sealed class Report
        {
            public string title { get; set; }
            public string message { get; set; }
            public string[][] failed { get; set; }
        }

        private sealed class ProgressWindow : Form
        {
            private readonly Label status = new Label { Text = "正在准备处理…", AutoSize = true };
            private readonly ProgressBar bar = new ProgressBar {
                Width = 320, Height = 18, Style = ProgressBarStyle.Marquee, AccessibleName = "清除进度"
            };
            private volatile int handled;
            private volatile int total = -1;
            private volatile bool queued;
            private bool finished;

            internal ProgressWindow()
            {
                Text = "正在清除 JPG HDR 增益图";
                Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
                Font = SystemFonts.MessageBoxFont;
                AutoScaleMode = AutoScaleMode.Font;
                AutoSize = true;
                AutoSizeMode = AutoSizeMode.GrowAndShrink;
                ControlBox = false;
                FormBorderStyle = FormBorderStyle.FixedDialog;
                StartPosition = FormStartPosition.CenterScreen;
                var layout = new TableLayoutPanel { ColumnCount = 1, AutoSize = true,
                    Dock = DockStyle.Fill, Padding = new Padding(16) };
                layout.Controls.Add(status);
                layout.Controls.Add(bar);
                Controls.Add(layout);
                FormClosing += delegate(object sender, FormClosingEventArgs e) {
                    if (!finished && e.CloseReason == CloseReason.UserClosing) e.Cancel = true;
                };
            }

            internal Report ProcessSelection(string[] paths)
            {
                Task<Report> work = null;
                using (var timer = new System.Windows.Forms.Timer { Interval = 100 })
                {
                    timer.Tick += delegate {
                        int count = total, done = handled;
                        if (count < 0) {
                            status.Text = queued ? "正在等待前一批照片处理完成…" : "正在准备处理…";
                            return;
                        }
                        status.Text = String.Format("已处理 {0} / {1}", done, count);
                        bar.Style = count == 1 && done == 0 ? ProgressBarStyle.Marquee : ProgressBarStyle.Continuous;
                        bar.Maximum = Math.Max(1, count);
                        bar.Value = done;
                    };
                    Shown += delegate {
                        timer.Start();
                        work = Task.Run(() => {
                            using (var mutex = new Mutex(false, @"Local\ClearJPGHDR.PhotoWorker"))
                            {
                                try {
                                    if (!mutex.WaitOne(0)) { queued = true; mutex.WaitOne(); }
                                } catch (AbandonedMutexException) { }
                                queued = false;
                                try { return ProcessPhotos(paths, (done, count) => { total = count; handled = done; }); }
                                finally { mutex.ReleaseMutex(); }
                            }
                        });
                        work.ContinueWith(completed => { finished = true; Close(); },
                                          TaskScheduler.FromCurrentSynchronizationContext());
                    };
                    ShowDialog();
                }
                return work.GetAwaiter().GetResult();
            }
        }

        private static Report ProcessPhotos(string[] paths, Action<int, int> progress)
        {
            string root = AppDomain.CurrentDomain.BaseDirectory;
            var start = new ProcessStartInfo(Path.Combine(root, @"runtime\python.exe"));
            start.Arguments = "-I -B -X utf8 \"" + Path.Combine(root, "clear_hdr.py") + "\" --stdin-paths --progress"
                + (ReadOption("BackupEnabled", false) ? " --backup" : "");
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;
            start.RedirectStandardInput = true;
            start.RedirectStandardOutput = start.RedirectStandardError = true;
            start.StandardOutputEncoding = start.StandardErrorEncoding = Encoding.UTF8;
            using (var process = Process.Start(start))
            {
                Task<string> output = process.StandardOutput.ReadToEndAsync();
                var errors = new StringBuilder();
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) {
                    if (e.Data == null) return;
                    string[] parts = e.Data.Split(' ');
                    int done, count;
                    if (parts.Length == 3 && parts[0] == "PROGRESS" && Int32.TryParse(parts[1], out done)
                        && Int32.TryParse(parts[2], out count) && done >= 0 && done <= count)
                        progress(done, count);
                    else errors.AppendLine(e.Data);
                };
                process.BeginErrorReadLine();
                var json = new JavaScriptSerializer { MaxJsonLength = Int32.MaxValue };
                byte[] selection = Encoding.UTF8.GetBytes(json.Serialize(paths));
                using (Stream input = process.StandardInput.BaseStream)
                    input.Write(selection, 0, selection.Length);
                process.WaitForExit();
                string error = errors.ToString().Trim();
                if (String.IsNullOrWhiteSpace(output.Result))
                    throw new IOException(error.Length == 0 ? "照片处理程序未返回结果。" : error);
                var report = json.Deserialize<Report>(output.Result);
                if (report == null || report.failed == null || String.IsNullOrEmpty(report.title)
                    || String.IsNullOrEmpty(report.message))
                    throw new IOException("无法读取照片处理结果。");
                if (process.ExitCode != (report.failed.Length == 0 ? 0 : 1))
                    throw new IOException("照片处理程序异常退出。\n" + error);
                return report;
            }
        }
    }

    internal sealed class SettingsWindow : Form
    {
        private readonly CheckBox menu = new CheckBox { Text = "显示「清除 JPG HDR 增益图」", AutoSize = true };
        private readonly CheckBox backup = new CheckBox { Text = "保留原图备份", AutoSize = true };
        private readonly Label menuLocation = new Label { AutoSize = true, ForeColor = SystemColors.GrayText };
        private readonly Label backupHint = new Label {
            AutoSize = true, ForeColor = SystemColors.GrayText,
            Text = "例如：照片.jpg_original。同名备份已存在时保留原图并提示失败。"
        };

        internal SettingsWindow()
        {
            Text = App.Title;
            Font = SystemFonts.MessageBoxFont;
            Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
            AutoScaleMode = AutoScaleMode.Font;
            AutoSize = true;
            AutoSizeMode = AutoSizeMode.GrowAndShrink;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            var layout = new TableLayoutPanel { AutoSize = true, ColumnCount = 1,
                Dock = DockStyle.Fill, Padding = new Padding(20, 14, 20, 18) };
            Controls.Add(layout);
            AddSection(layout, "资源管理器右键菜单", menu, menuLocation);
            AddSection(layout, "照片处理", backup, backupHint);
            AddSection(layout, "画质说明", new Label { AutoSize = true,
                Text = "清除增益图不会降低 JPG 主图的清晰度或分辨率，但会移除原来的 HDR 高光效果。工具不会重新压缩主图。\n\n"
                    + "实测三份样本，处理前后的主图压缩数据和解码后的每个像素都相同，EXIF 和已有的 ICC 色彩配置也保持一致。" });
            menu.Checked = ExplorerMenu.IsEnabled();
            backup.Checked = App.ReadOption("BackupEnabled", false);
            UpdateHints();
            menu.Click += delegate { Save(menu, "MenuEnabled"); };
            backup.Click += delegate { Save(backup, "BackupEnabled"); };
        }

        private static void AddSection(TableLayoutPanel layout, string title, params Control[] controls)
        {
            var group = new GroupBox { Text = title, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink,
                Dock = DockStyle.Fill, MinimumSize = new Size(420, 0), Padding = new Padding(14, 6, 14, 12),
                Margin = new Padding(0, 4, 0, 8) };
            var contents = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, WrapContents = false,
                Dock = DockStyle.Fill, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink };
            foreach (var control in controls) {
                if (control is Label) {
                    control.MaximumSize = new Size(400, 0);
                    control.Margin = new Padding(3, 6, 3, 3);
                }
                contents.Controls.Add(control);
            }
            group.Controls.Add(contents);
            layout.Controls.Add(group);
        }

        private void UpdateHints()
        {
            backupHint.Visible = backup.Checked;
            menuLocation.Visible = menu.Checked;
            menuLocation.Text = Environment.OSVersion.Version.Build >= 22000 && !ExplorerMenu.IsModernEnabled()
                ? "菜单位置：右键 → 显示更多选项" : "菜单位置：右键菜单";
        }

        private void Save(CheckBox option, string name)
        {
            bool previous = App.ReadOption(name, option == menu);
            try
            {
                if (option == menu) ExplorerMenu.SetEnabled(menu.Checked);
                App.WriteOption(name, option.Checked);
            }
            catch (Exception e)
            {
                option.Checked = option == menu ? ExplorerMenu.IsEnabled() : previous;
                MessageBox.Show(this, e.Message, "无法保存设置", MessageBoxButtons.OK, MessageBoxIcon.Warning);
            }
            UpdateHints();
        }
    }
}
