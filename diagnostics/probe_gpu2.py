import ctypes
from ctypes import c_void_p, c_uint, c_int, c_long, c_ulong, c_ulonglong, c_wchar, POINTER, byref, Structure, c_bool

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

d3d11 = ctypes.CDLL("d3d11")
D3D11CreateDevice = d3d11.D3D11CreateDevice
D3D11CreateDevice.restype = ctypes.HRESULT
D3D11CreateDevice.argtypes = [c_void_p, c_uint, c_void_p, c_uint, c_void_p, c_uint, c_uint,
                              POINTER(c_void_p), POINTER(c_uint), POINTER(c_void_p)]

dxgi = ctypes.CDLL("dxgi")
CreateDXGIFactory1 = dxgi.CreateDXGIFactory1
CreateDXGIFactory1.restype = ctypes.HRESULT
CreateDXGIFactory1.argtypes = [POINTER(GUID), POINTER(c_void_p)]
IID_FACTORY1 = GUID(0x770aae78, 0xf26f, 0x4dba, (ctypes.c_ubyte * 8)(0xa8, 0x29, 0x25, 0x3c, 0x83, 0xd1, 0xb3, 0x87))
factory = c_void_p()
hr = CreateDXGIFactory1(byref(IID_FACTORY1), byref(factory))
print(f"CreateDXGIFactory1 hr=0x{hr & 0xFFFFFFFF:08X}")

EnumAdapters1 = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_uint, POINTER(c_void_p))(vtable(factory, 8))
for i in range(8):
    ad = c_void_p()
    if EnumAdapters1(factory, i, byref(ad)) != 0:
        break
    desc = ADAPTER_DESC1()
    ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(ADAPTER_DESC1))(vtable(ad, 6))(ad, byref(desc))
    soft = desc.Flags & 2
    print(f"Adapter[{i}]: {desc.Description} vendor=0x{desc.VendorId:X} vram={desc.DedicatedVideoMemory>>20}MB software_flag={soft}")
    EnumOutputs = ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, c_uint, POINTER(c_void_p))(vtable(ad, 3))
    for j in range(8):
        od = c_void_p()
        if EnumOutputs(ad, j, byref(od)) != 0:
            break
        odesc = OUTPUT_DESC()
        ctypes.WINFUNCTYPE(ctypes.HRESULT, c_void_p, POINTER(OUTPUT_DESC))(vtable(od, 3))(od, byref(odesc))
        r = odesc.DesktopCoordinates
        print(f"  Output[{j}]: {odesc.DeviceName} rect=({r.l},{r.t},{r.r},{r.b}) attached={odesc.AttachedToDesktop}")
    # try D3D11 on this adapter (driver type UNKNOWN=0 with explicit adapter)
    fls = (c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
    dev = c_void_p(); fl = c_uint(); ctx = c_void_p()
    hr = D3D11CreateDevice(ad, 0, None, 0, fls, 7, 7, byref(dev), byref(fl), byref(ctx))
    print(f"  D3D11CreateDevice: hr=0x{hr & 0xFFFFFFFF:08X} feature_level=0x{fl.value:X}")
