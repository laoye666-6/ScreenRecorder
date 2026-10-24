import ctypes
import comtypes
from comtypes import COMError
from bettercam._libs.dxgi import (
    IDXGIFactory1, IDXGIAdapter1, IDXGIOutput, IDXGIOutput1, IDXGIOutputDuplication,
    DXGI_ADAPTER_DESC1, DXGI_OUTPUT_DESC,
)
from bettercam._libs.d3d11 import ID3D11Device, ID3D11DeviceContext
from bettercam.core.device import Device

POINTER = ctypes.POINTER
byref = ctypes.byref

factory = POINTER(IDXGIFactory1)()
hr = ctypes.windll.dxgi.CreateDXGIFactory1(IDXGIFactory1._iid_, byref(factory))
print(f"factory hr=0x{hr & 0xFFFFFFFF:08X}")

i = 0
while True:
    ad = POINTER(IDXGIAdapter1)()
    try:
        factory.EnumAdapters1(i, byref(ad))
    except COMError as e:
        print(f"EnumAdapters1({i}) stop hr=0x{e.args[0] & 0xFFFFFFFF:08X}")
        break
    desc = DXGI_ADAPTER_DESC1()
    ad.GetDesc1(byref(desc))
    print(f"Adapter[{i}]: {desc.Description} vendor=0x{desc.VendorId:X} vram={desc.DedicatedVideoMemory>>20}MB software={bool(desc.Flags & 2)}")

    outs = []
    j = 0
    while True:
        o = POINTER(IDXGIOutput)()
        try:
            ad.EnumOutputs(j, byref(o))
        except COMError:
            break
        od = DXGI_OUTPUT_DESC()
        o.GetDesc(byref(od))
        r = od.DesktopCoordinates
        print(f"  Output[{j}]: {od.DeviceName} rect=({r.left},{r.top},{r.right},{r.bottom}) attached={od.AttachedToDesktop} rot={od.Rotation}")
        outs.append(o)
        j += 1

    # D3D11 feature level per adapter
    fls = (ctypes.c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
    dev = POINTER(ID3D11Device)()
    fl = ctypes.c_uint()
    ctx = POINTER(ID3D11DeviceContext)()
    hr = ctypes.windll.d3d11.D3D11CreateDevice(
        ad, 0, None, 0, byref(fls), 7, 7, byref(dev), byref(fl), byref(ctx))
    if hr == 0:
        print(f"  D3D11CreateDevice OK fl=0x{fl.value:X}")
        for k, o in enumerate(outs):
            try:
                o1 = o.QueryInterface(IDXGIOutput1)
                dup = POINTER(IDXGIOutputDuplication)()
                o1.DuplicateOutput(dev, byref(dup))
                print(f"  Output[{k}] DuplicateOutput: OK !!!")
                del dup
            except COMError as e:
                print(f"  Output[{k}] DuplicateOutput FAILED hr=0x{e.args[0] & 0xFFFFFFFF:08X}")
        del dev, ctx
    else:
        print(f"  D3D11CreateDevice FAILED hr=0x{hr & 0xFFFFFFFF:08X}")
    i += 1

# default hardware adapter
fls = (ctypes.c_uint * 7)(0x9100, 0x9200, 0x9300, 0xA000, 0xA100, 0xB000, 0xB100)
dev = POINTER(ID3D11Device)(); fl = ctypes.c_uint(); ctx = POINTER(ID3D11DeviceContext)()
hr = ctypes.windll.d3d11.D3D11CreateDevice(None, 1, None, 0, byref(fls), 7, 7, byref(dev), byref(fl), byref(ctx))
print(f"Default HARDWARE device: hr=0x{hr & 0xFFFFFFFF:08X} fl=0x{fl.value:X}")
