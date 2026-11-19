// Build in an isolated test folder, next to the DLL; copy this exe as ClearJPGHDR.exe.
// Its --process entry records the received argv instead of processing photographs.
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <shlobj.h>
#include <wrl.h>
#include <cstdio>
#include <string>
#include <vector>
using Microsoft::WRL::ComPtr;
static const CLSID commandId = {0x7fde7152, 0xb042, 0x41b3, {0xb9,0x28,0x54,0xe5,0x2a,0x32,0x4c,0x91}};
#define CHECK(x) do { if (!(x)) { fwprintf(stderr,L"Check at line %d failed\n",__LINE__);return 1; } } while (0)

int wmain(int argc, wchar_t** argv) {
    if (argc > 1 && !wcscmp(argv[1], L"--process")) {
        FILE* f = nullptr;
        if (_wfopen_s(&f, L"argv.txt", L"wb") || !f) return 2;
        for (int i = 2; i < argc; ++i) fwrite(argv[i], sizeof(wchar_t), wcslen(argv[i])+1, f);
        fclose(f);
        return 0;
    }
    CHECK(argc == 2);
    HKEY test = nullptr, state = nullptr;
    wchar_t testKey[100];
    swprintf_s(testKey,L"Software\\ClearJPGHDR-ShellTest-%lu",GetCurrentProcessId());
    CHECK(RegCreateKeyExW(HKEY_CURRENT_USER, testKey,0,nullptr,REG_OPTION_VOLATILE,KEY_ALL_ACCESS,
                          nullptr,&test,nullptr)==ERROR_SUCCESS);
    CHECK(RegOverridePredefKey(HKEY_CURRENT_USER,test)==ERROR_SUCCESS);
    CHECK(RegCreateKeyExW(HKEY_CURRENT_USER,L"Software\\ClearJPGHDR\\Explorer",0,nullptr,REG_OPTION_VOLATILE,
                          KEY_ALL_ACCESS,nullptr,&state,nullptr)==ERROR_SUCCESS);
    DWORD on=1;
    CHECK(RegSetValueExW(state,L"Enabled",0,REG_DWORD,reinterpret_cast<BYTE*>(&on),sizeof(on))==ERROR_SUCCESS);
    CHECK(SUCCEEDED(CoInitializeEx(nullptr,COINIT_APARTMENTTHREADED)));
    HMODULE dll = LoadLibraryW(argv[1]); CHECK(dll);
    auto get = reinterpret_cast<HRESULT(STDAPICALLTYPE*)(REFCLSID,REFIID,void**)>(GetProcAddress(dll,"DllGetClassObject"));
    ComPtr<IClassFactory> factory; ComPtr<IExplorerCommand> command;
    CHECK(get && SUCCEEDED(get(commandId, IID_PPV_ARGS(&factory))));
    CHECK(SUCCEEDED(factory->CreateInstance(nullptr,IID_PPV_ARGS(&command))));
    wchar_t dir[MAX_PATH]; CHECK(GetCurrentDirectoryW(MAX_PATH,dir));
    std::vector<std::wstring> paths;
    std::vector<PCIDLIST_ABSOLUTE> ids;
    for (auto name : { L"空 格.jpg", L"quoted & %name% (2).JPEG", L"file.txt", L"current folder" }) {
        std::wstring path=std::wstring(dir)+L"\\"+name;
        if (!wcscmp(name,L"current folder")) CHECK(CreateDirectoryW(path.c_str(),nullptr));
        else { HANDLE h=CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,0,nullptr); CHECK(h!=INVALID_HANDLE_VALUE); CloseHandle(h); }
        PIDLIST_ABSOLUTE id=nullptr;
        CHECK(SUCCEEDED(SHParseDisplayName(path.c_str(),nullptr,&id,0,nullptr)));
        ids.push_back(id); paths.push_back(path);
    }
    ComPtr<IShellItemArray> items, text;
    CHECK(SUCCEEDED(SHCreateShellItemArrayFromIDLists(static_cast<UINT>(ids.size()),ids.data(),&items)));
    CHECK(SUCCEEDED(SHCreateShellItemArrayFromIDLists(1,&ids[2],&text)));
    EXPCMDSTATE enabled; CHECK(SUCCEEDED(command->GetState(text.Get(),FALSE,&enabled)) && enabled==ECS_HIDDEN);
    CHECK(SUCCEEDED(command->GetState(items.Get(),FALSE,&enabled)) && enabled==ECS_ENABLED);
    CHECK(SUCCEEDED(command->Invoke(items.Get(),nullptr)));
    std::wstring expected;
    for (int i : {0,1,3}) { expected+=paths[i]; expected.push_back(0); }
    bool received=false;
    for (int attempt=0;attempt<100 && !received;++attempt) {
        FILE* f=nullptr;
        if (!_wfopen_s(&f,L"argv.txt",L"rb") && f) {
            std::vector<wchar_t> bytes(expected.size()+1);
            size_t count=fread(bytes.data(),sizeof(wchar_t),bytes.size(),f); fclose(f);
            received=(count==expected.size() && !memcmp(bytes.data(),expected.data(),count*sizeof(wchar_t)));
        }
        if (!received) Sleep(50);
    }
    CHECK(received);
    on=0;
    CHECK(RegSetValueExW(state,L"Enabled",0,REG_DWORD,reinterpret_cast<BYTE*>(&on),sizeof(on))==ERROR_SUCCESS);
    CHECK(SUCCEEDED(command->GetState(items.Get(),FALSE,&enabled)) && enabled==ECS_HIDDEN);
    for (auto id:ids) CoTaskMemFree(const_cast<PIDLIST_ABSOLUTE>(id));
    text.Reset(); items.Reset(); command.Reset(); factory.Reset(); CoUninitialize(); FreeLibrary(dll);
    RegCloseKey(state); RegOverridePredefKey(HKEY_CURRENT_USER,nullptr); RegCloseKey(test);
    RegDeleteTreeW(HKEY_CURRENT_USER,testKey);
    puts("PASS: selection, folder, file filter, Unicode argv, menu switch");
    return 0;
}
