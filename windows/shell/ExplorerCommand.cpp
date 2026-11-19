// A file system selection is passed directly to the worker, never through a shell.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <shlguid.h>
#include <wrl.h>
#include <string>
#include <new>

using Microsoft::WRL::ComPtr;
using Microsoft::WRL::RuntimeClass;
using Microsoft::WRL::RuntimeClassFlags;
using Microsoft::WRL::ClassicCom;
extern "C" IMAGE_DOS_HEADER __ImageBase;
static const CLSID commandId = {0x7fde7152, 0xb042, 0x41b3, {0xb9,0x28,0x54,0xe5,0x2a,0x32,0x4c,0x91}};
static LONG objects;
static constexpr wchar_t title[] = L"清除 JPG HDR 增益图";

static bool enabled() {
    DWORD value = 0, size = sizeof(value);
    return RegGetValueW(HKEY_CURRENT_USER, L"Software\\ClearJPGHDR\\Explorer", L"Enabled",
                        RRF_RT_REG_DWORD, nullptr, &value, &size) == ERROR_SUCCESS && value == 1;
}

static std::wstring hostPath() {
    wchar_t path[32768];
    DWORD n = GetModuleFileNameW(reinterpret_cast<HMODULE>(&__ImageBase), path, ARRAYSIZE(path));
    if (!n || n >= ARRAYSIZE(path)) return {};
    std::wstring file(path, n);
    return file.substr(0, file.find_last_of(L'\\') + 1) + L"ClearJPGHDR.exe";
}

static bool eligible(IShellItem* item, std::wstring& path) {
    SFGAOF flags;
    if (FAILED(item->GetAttributes(SFGAO_FILESYSTEM | SFGAO_FOLDER, &flags))
        || !(flags & SFGAO_FILESYSTEM)) return false;
    PWSTR raw = nullptr;
    if (FAILED(item->GetDisplayName(SIGDN_FILESYSPATH, &raw))) return false;
    path.assign(raw);
    CoTaskMemFree(raw);
    auto ext = PathFindExtensionW(path.c_str());
    return (flags & SFGAO_FOLDER) || !_wcsicmp(ext, L".jpg") || !_wcsicmp(ext, L".jpeg");
}

static HRESULT launch(const std::wstring& exe, std::wstring& args) {
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(exe.c_str(), &args[0], nullptr, nullptr, FALSE, 0, nullptr, nullptr,
                        &startup, &process)) return HRESULT_FROM_WIN32(GetLastError());
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return S_OK;
}

class Command final : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IExplorerCommand, IObjectWithSite> {
    ComPtr<IUnknown> site;
    HRESULT background(ComPtr<IShellItem>& item) {
        ComPtr<IFolderView> view;
        HRESULT hr = site ? IUnknown_QueryService(site.Get(), SID_SFolderView, IID_PPV_ARGS(&view)) : E_FAIL;
        if (FAILED(hr)) return hr;
        ComPtr<IPersistFolder2> folder;
        hr = view->GetFolder(IID_PPV_ARGS(&folder));
        if (FAILED(hr)) return hr;
        PIDLIST_ABSOLUTE pidl = nullptr;
        hr = folder->GetCurFolder(&pidl);
        if (SUCCEEDED(hr)) hr = SHCreateItemFromIDList(pidl, IID_PPV_ARGS(&item));
        CoTaskMemFree(pidl);
        return hr;
    }
    // A Windows filesystem name cannot contain a quote. Double trailing backslashes
    // (drive roots) before the closing quote, following CommandLineToArgvW rules.
    static std::wstring argument(const std::wstring& path) {
        size_t last = path.find_last_not_of(L'\\');
        size_t trailing = last == std::wstring::npos ? path.size() : path.size() - 1 - last;
        return L" \"" + path + std::wstring(trailing, L'\\') + L"\"";
    }
    HRESULT selection(IShellItemArray* items, bool perform, bool& found) {
        if (!enabled()) return S_FALSE;
        DWORD count = 0;
        HRESULT hr = items ? items->GetCount(&count) : S_OK;
        if (FAILED(hr)) return hr;
        std::wstring exe, prefix, args;
        if (perform) {
            exe = hostPath();
            if (exe.empty()) return E_FAIL;
            prefix = L"\"" + exe + L"\" --process";
            args = prefix;
        }
        for (DWORD i = 0; i < (count ? count : 1); ++i) {
            ComPtr<IShellItem> item;
            hr = count ? items->GetItemAt(i, &item) : background(item);
            if (FAILED(hr)) continue;
            std::wstring path;
            if (!eligible(item.Get(), path)) continue;
            found = true;
            if (!perform) return S_OK;
            auto arg = argument(path);
            if (prefix.size() + arg.size() > 32766) return HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE);
            if (args.size() + arg.size() > 32766) {
                hr = launch(exe, args);
                if (FAILED(hr)) return hr;
                args = prefix;
            }
            args += arg;
        }
        return perform && found ? launch(exe, args) : S_OK;
    }
public:
    Command() { InterlockedIncrement(&objects); }
    ~Command() { InterlockedDecrement(&objects); }
    IFACEMETHODIMP GetTitle(IShellItemArray*, PWSTR* value) override { return SHStrDupW(title, value); }
    IFACEMETHODIMP GetIcon(IShellItemArray*, PWSTR* value) override {
        try { return SHStrDupW((hostPath() + L",0").c_str(), value); } catch (...) { return E_OUTOFMEMORY; }
    }
    IFACEMETHODIMP GetToolTip(IShellItemArray*, PWSTR* value) override { *value = nullptr; return E_NOTIMPL; }
    IFACEMETHODIMP GetCanonicalName(GUID* value) override { *value = commandId; return S_OK; }
    IFACEMETHODIMP GetState(IShellItemArray* items, BOOL, EXPCMDSTATE* value) override {
        *value = ECS_HIDDEN;
        bool found = false;
        try { selection(items, false, found); } catch (...) { return E_OUTOFMEMORY; }
        if (found) *value = ECS_ENABLED;
        return S_OK;
    }
    IFACEMETHODIMP Invoke(IShellItemArray* items, IBindCtx*) override {
        bool found = false;
        try { return selection(items, true, found); } catch (...) { return E_OUTOFMEMORY; }
    }
    IFACEMETHODIMP GetFlags(EXPCMDFLAGS* flags) override { *flags = ECF_DEFAULT; return S_OK; }
    IFACEMETHODIMP EnumSubCommands(IEnumExplorerCommand** commands) override { *commands = nullptr; return E_NOTIMPL; }
    IFACEMETHODIMP SetSite(IUnknown* value) override { site = value; return S_OK; }
    IFACEMETHODIMP GetSite(REFIID iid, void** value) override {
        *value = nullptr;
        return site ? site->QueryInterface(iid, value) : E_FAIL;
    }
};

class Factory final : public RuntimeClass<RuntimeClassFlags<ClassicCom>, IClassFactory> {
public:
    Factory() { InterlockedIncrement(&objects); }
    ~Factory() { InterlockedDecrement(&objects); }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** value) override {
        *value = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;
        auto cmd = Microsoft::WRL::Make<Command>();
        return cmd ? cmd->QueryInterface(iid, value) : E_OUTOFMEMORY;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) InterlockedIncrement(&objects); else InterlockedDecrement(&objects);
        return S_OK;
    }
};

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** value) {
    *value = nullptr;
    if (clsid != commandId) return CLASS_E_CLASSNOTAVAILABLE;
    auto factory = Microsoft::WRL::Make<Factory>();
    return factory ? factory->QueryInterface(iid, value) : E_OUTOFMEMORY;
}
STDAPI DllCanUnloadNow() { return InterlockedCompareExchange(&objects, 0, 0) == 0 ? S_OK : S_FALSE; }
