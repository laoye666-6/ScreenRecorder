import ctypes
from ctypes import c_void_p, c_uint, c_int, c_ulong, c_ulonglong, c_wchar, POINTER, byref, Structure, c_long, c_bool

d3d11 = ctypes.CDLL("d3d11")
D3D11CreateDevice = d3d11.D3D11CreateDevice
D3D11CreateDevice.restype = ctypes.HRESULT
D3D11CreateDevice.argtypes = [c_void_p, c_uint, c_void_p, c_uint, c_void_p, c_uint, c_uint,
                              POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p)]

def try_create(adapter, dtype, name):
    fls = (c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
    dev = c_void_p(); fl = c_uint(); ctx = c_void_p()
    hr = D3D11CreateDevice(adapter, dtype, None, 0, fls, 7, 7, byref(dev), byref(fl), byref(ctx))
    print(f"{name}: hr=0x{hr & 0xFFFFFFFF:08X} fl=0x{fl.value:X}" if hr >= 0 or fl.value else
          f"{name}: hr=0x{hr & 0xFFFFFFFF:08X}")
    return hr

try_create(None, 1, "D3D11 HARDWARE default")
try_create(None, 5, "D3D11 WARP (software)")

# ---- DXGI enumeration via raw vtable ----
class RECT(Structure):
    _fields_ = [("l", c_long), ("t", c_long), ("r", c_long), ("b", c_long)]

class ADAPTER_DESC1(Structure):
    _fields_ = [("Description", c_wchar * 128), ("VendorId", c_uint), ("DeviceId", c_uint),
                ("SubSysId", c_uint), ("Revision", c_uint),
                ("DedicatedVideoMemory", c_ulonglong), ("DedicatedSystemMemory", c_ulonglong),
                ("SharedSystemMemory", c_ulonglong), ("LuidLow", c_ulong), ("LuidHigh", c_long),
                ("Flags", c_uint)]

class OUTPUT_DESC(Structure):
    _fields_ = [("DeviceName", c_wchar * 32), ("DesktopCoordinates", RECT),
                ("AttachedToDesktop", c_bool), ("Rotation", c_int)]

def vtable(obj, index):
    return ctypes.cast(ctypes.cast(obj, POINTER(c_void_p)).contents.value, POINTER(c_void_p)).contents[index]

def call_vt(obj, index, restype, argtypes, *args):
    fn = ctypes.WINFUNCTYPE(restype, *([c_void_p] + argtypes))(vtable(obj, index))
    return fn(obj, *args)

dxgi = ctypes.CDLL("dxgi")
CreateDXGIFactory1 = dxgi.CreateDXGIFactory1
CreateDXGIFactory1.restype = ctypes.HRESULT
CreateDXGIFactory1.argtypes = [c_void_p, POINTER(c_void_p)]
factory = c_void_p()
hr = CreateDXGIFactory1(byref(ctypes.c_void_p(0)), byref(factory))
# IID_IDXGIFactory1 = {770aae78-f26f-4dba-a829-253c83d1b387}
iid = ctypes.create_string_buffer(bytes.fromhex("78ae0a7f6ff2ba4da829253c83d1b387")[::-1])  # wrong order; build GUID properly
class GUID(Structure):
    _fields_ = [("d1", c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]
g = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
hr = CreateDXGIFactory1(byref(g), byref(factory))
print(f"CreateDXGIFactory1: hr=0x{hr & 0xFFFFFFFF:08X}")
EnumAdapters1 = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_uint, POINTER(c_void_p))(vtable(factory, 8))
for i in range(6):
    ad = c_void_p()
    hr = EnumAdapters1(factory, i, byref(ad))
    if hr != 0:
        break
    desc = ADAPTER_DESC1()
    GetDesc1 = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(ADAPTER_DESC1))(vtable(ad, 6))
    GetDesc1(ad, byref(desc))
    print(f"Adapter[{i}]: {desc.Description} vendor=0x{desc.VendorId:X} flags=0x{desc.Flags:X}")
    EnumOutputs = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_uint, POINTER(c_void_p))(vtable(ad, 3))
    for j in range(6):
        od = c_void_p()
        hr = EnumOutputs(ad, j, byref(od))
        if hr != 0:
            break
        odesc = OUTPUT_DESC()
        GetODesc = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(OUTPUT_DESC))(vtable(od, 3))
        GetODesc(od, byref(odesc))
        r = odesc.DesktopCoordinates
        print(f"  Output[{j}]: {odesc.DeviceName} rect=({r.l},{r.t},{r.r},{r.b}) attached={odesc.AttachedToDesktop} rot={odesc.Rotation}")
        # DuplicateOutput test: IID_ID3D11Device needed - skip; instead try D3D11 create on this adapter
        try_create(ad, 1, f"    D3D11 on adapter[{i}]")
