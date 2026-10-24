import ctypes
from ctypes import c_void_p, c_uint, c_int, c_long, c_ulong, c_ulonglong, c_wchar, POINTER, byref, Structure, c_bool

ole32 = ctypes.windll.ole32
hr = ole32.CoInitializeEx(None, 0x0)
print("CoInitializeEx:", hex(hr & 0xFFFFFFFF))

class GUID(Structure):
    _fields_ = [("d1", c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]

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
    intf = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(c_void_p)))
    return intf.contents[index]

def mkfunc(obj, index, restype, *argtypes):
    fn = ctypes.WINFUNCTYPE(restype, *argtypes)(vtable(obj, index))
    fn.errcheck = lambda r, f, a: r  # don't raise on negative HRESULT
    return fn

d3d11 = ctypes.CDLL("d3d11")
D3D11CreateDevice = d3d11.D3D11CreateDevice
D3D11CreateDevice.restype = c_long
D3D11CreateDevice.argtypes = [c_void_p, c_uint, c_void_p, c_uint, c_void_p, c_uint, c_uint,
                              POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p)]
D3D11CreateDevice.errcheck = lambda r, f, a: r

dxgi = ctypes.CDLL("dxgi")
CreateDXGIFactory1 = dxgi.CreateDXGIFactory1
CreateDXGIFactory1.restype = c_long
CreateDXGIFactory1.argtypes = [POINTER(GUID), POINTER(c_void_p)]
CreateDXGIFactory1.errcheck = lambda r, f, a: r
IID_FACTORY1 = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
factory = c_void_p()
hr = CreateDXGIFactory1(byref(IID_FACTORY1), byref(factory))
print(f"CreateDXGIFactory1 hr=0x{hr & 0xFFFFFFFF:08X} factory={'OK' if factory.value else 'NULL'}")

EnumAdapters1_t = ctypes.WINFUNCTYPE(c_long, c_uint, POINTER(c_void_p))
EnumAdapters1 = EnumAdapters1_t(vtable(factory, 12))
EnumAdapters1.errcheck = lambda r, f, a: r

for i in range(8):
    ad = c_void_p()
    hr = EnumAdapters1(i, byref(ad))
    if hr != 0:
        print(f"EnumAdapters1({i}) hr=0x{hr & 0xFFFFFFFF:08X} -> stop")
        break
    desc = ADAPTER_DESC1()
    GetDesc1 = mkfunc(ad, 10, c_long, POINTER(ADAPTER_DESC1))
    GetDesc1(byref(desc))
    print(f"Adapter[{i}]: {desc.Description} vendor=0x{desc.VendorId:X} vram={desc.DedicatedVideoMemory>>20}MB software={bool(desc.Flags & 2)}")
    EnumOutputs = mkfunc(ad, 7, c_long, c_uint, POINTER(c_void_p))
    for j in range(8):
        od = c_void_p()
        hr = EnumOutputs(j, byref(od))
        if hr != 0:
            break
        odesc = OUTPUT_DESC()
        GetODesc = mkfunc(od, 7, c_long, POINTER(OUTPUT_DESC))
        GetODesc(byref(odesc))
        r = odesc.DesktopCoordinates
        print(f"  Output[{j}]: {odesc.DeviceName} rect=({r.l},{r.t},{r.r},{r.b}) attached={odesc.AttachedToDesktop} rot={odesc.Rotation}")
    fls = (c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
    dev = c_void_p(); fl = c_uint(); ctx = c_void_p()
    hr = D3D11CreateDevice(ad, 0, None, 0, fls, 7, 7, byref(dev), byref(fl), byref(ctx))
    print(f"  D3D11CreateDevice: hr=0x{hr & 0xFFFFFFFF:08X} fl=0x{fl.value:X} {'OK' if hr==0 and dev.value else 'FAIL'}")

# default-adapter device
fls = (c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
dev = c_void_p(); fl = c_uint(); ctx = c_void_p()
hr = D3D11CreateDevice(None, 1, None, 0, fls, 7, 7, byref(dev), byref(fl), byref(ctx))
print(f"D3D11 default HARDWARE: hr=0x{hr & 0xFFFFFFFF:08X} fl=0x{fl.value:X}")
