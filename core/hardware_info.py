"""Read-only hardware inventory and conservative configuration advice."""
import ctypes
import os
import platform
import subprocess
import sys
import uuid

import psutil

GIB = 1024 ** 3
VENDORS = {0x10DE: "NVIDIA", 0x1002: "AMD", 0x8086: "Intel"}


def _macos_cpu() -> str:
    try:
        out = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True, timeout=3).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return platform.processor() or platform.machine() or "未知"


def _macos_gpus() -> list:
    """通过 system_profiler 读取显卡型号。"""
    try:
        out = subprocess.run(["system_profiler", "-json", "SPDisplaysDataType"],
                             capture_output=True, text=True, timeout=20).stdout
        import json
        displays = json.loads(out).get("SPDisplaysDataType", []) or []
    except Exception:
        return []
    gpus = []
    for item in displays:
        name = (item.get("sppci_model") or item.get("_name")
                or item.get("spdisplays_chipset_model") or "GPU")
        vram = item.get("spdisplays_vram") or item.get("spdisplays_vram_shared") or ""
        gpus.append({
            "name": name, "vendor": "Apple" if "Apple" in name else "其他",
            "vendor_id": -1, "dedicated_bytes": 0, "shared_bytes": 0,
            "vram_text": vram,
        })
    return gpus



def windows_adapters():
    """DXGI uses SIZE_T memory counts; avoid the 32-bit WMI AdapterRAM field."""
    from ctypes import wintypes as w

    class GUID(ctypes.Structure):
        _fields_ = [("data", ctypes.c_ubyte * 16)]

    class LUID(ctypes.Structure):
        _fields_ = [("low", w.DWORD), ("high", w.LONG)]

    class Desc(ctypes.Structure):
        _fields_ = [
            ("name", w.WCHAR * 128), ("vendor", w.UINT), ("device", w.UINT),
            ("subsystem", w.UINT), ("revision", w.UINT),
            ("dedicated", ctypes.c_size_t), ("system", ctypes.c_size_t),
            ("shared", ctypes.c_size_t), ("luid", LUID), ("flags", w.UINT),
        ]

    def method(pointer, slot, result, *args):
        table = ctypes.cast(pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        return ctypes.WINFUNCTYPE(result, ctypes.c_void_p, *args)(table[slot])

    def release(pointer):
        method(pointer, 2, w.ULONG)(pointer)

    dxgi = ctypes.WinDLL(os.path.join(os.environ["SystemRoot"], "System32", "dxgi.dll"))
    create = dxgi.CreateDXGIFactory1
    create.argtypes = [ctypes.POINTER(GUID), ctypes.POINTER(ctypes.c_void_p)]
    create.restype = ctypes.c_long
    iid = GUID.from_buffer_copy(uuid.UUID("770aae78-f26f-4dba-a829-253c83d1b387").bytes_le)
    factory = ctypes.c_void_p()
    if create(ctypes.byref(iid), ctypes.byref(factory)) < 0:
        raise OSError("DXGI factory initialization failed")
    result = []
    try:
        enum = method(factory, 12, ctypes.c_long, w.UINT, ctypes.POINTER(ctypes.c_void_p))
        for index in range(64):
            adapter = ctypes.c_void_p()
            status = enum(factory, index, ctypes.byref(adapter))
            if status & 0xFFFFFFFF == 0x887A0002:
                break
            if status < 0:
                raise OSError(f"DXGI enumeration failed: {status}")
            try:
                desc = Desc()
                get_desc = method(adapter, 10, ctypes.c_long, ctypes.POINTER(Desc))
                if get_desc(adapter, ctypes.byref(desc)) < 0:
                    raise OSError("DXGI adapter description unavailable")
                if desc.flags & 2:
                    continue
                result.append({
                    "name": desc.name, "vendor": VENDORS.get(desc.vendor, "其他／虚拟适配器"),
                    "vendor_id": desc.vendor, "dedicated_bytes": desc.dedicated,
                    "shared_bytes": desc.shared,
                })
            finally:
                release(adapter)
    finally:
        release(factory)

    # DXGI 会把同一块显卡枚举多次（不同输出路径 / 混合显卡），按 名称+厂商 合并
    return merge_duplicate_gpus(result)


def merge_duplicate_gpus(adapters):
    """合并重复的显卡条目：按 名称+厂商 去重，显存取较大值"""
    unique = {}
    for gpu in adapters:
        key = (gpu["name"], gpu["vendor_id"])
        merged = unique.get(key)
        if merged is None:
            unique[key] = gpu
            continue
        merged["dedicated_bytes"] = max(merged["dedicated_bytes"], gpu["dedicated_bytes"])
        merged["shared_bytes"] = max(merged["shared_bytes"], gpu["shared_bytes"])
    return list(unique.values())


def recommend(info):
    """Heuristics, not benchmarks or claims about current GPU utilization."""
    if platform.system() == "Darwin":
        return {
            "settings": dict(model_type="paraformer", qwen_quantization="q5_k",
                             onnx_provider="CPU", llm_use_gpu=False),
            "reason": "macOS 当前使用离线 ONNX 模型（Paraformer），走 CPU 推理；"
                      "GGUF 引擎暂未在 macOS 构建，ONNX 运行库后端为 CoreML/CPU。",
            "gpu": info["gpus"][0]["name"] if info.get("gpus") else None,
        }
    settings = dict(model_type="sensevoice", qwen_quantization="q5_k",
                    onnx_provider="CPU", llm_use_gpu=False)
    gpu = max((g for g in info["gpus"] if g["vendor_id"] in VENDORS),
              key=lambda g: g["dedicated_bytes"], default=None)
    if info.get("gpu_error"):
        reason = "显卡检测未完成，先使用 SenseVoice CPU；检测失败不等于没有显卡。"
    elif gpu is None:
        reason = "未发现支持建议的 AMD／NVIDIA／Intel 硬件显卡，建议 SenseVoice CPU。"
    elif not info["vulkan_loader"]:
        reason = "检测到显卡，但未找到 Vulkan 驱动入口；先用 SenseVoice CPU。"
    elif info["ram_total"] < 8 * GIB or info["ram_available"] < 3 * GIB:
        reason = "系统总内存或当前可用内存较少，建议先使用 SenseVoice CPU。"
    else:
        settings.update(onnx_provider="AUTO", llm_use_gpu=True)
        if gpu["dedicated_bytes"] >= 4 * GIB:
            settings.update(model_type="qwen_asr", qwen_quantization="q5_k")
            reason = "专用显存至少 4 GiB，建议先试 Qwen3-ASR Q5_K；编码器 CPU、解码器 GPU。"
        elif gpu["dedicated_bytes"] >= 2 * GIB:
            settings.update(model_type="qwen_asr", qwen_quantization="q4_k")
            reason = "专用显存 2–4 GiB，建议先试 Qwen3-ASR Q4_K；显存紧张时改用 Fun-ASR-Nano。"
        else:
            settings.update(model_type="fun_asr_nano")
            reason = "专用显存较少或主要使用共享内存，先试 Fun-ASR-Nano；低延迟优先可选 SenseVoice。"
            if info["ram_total"] >= 16 * GIB:
                reason += "也可试 Qwen Q4_K，但需比较实际延迟。"
    return {"settings": settings, "reason": reason, "gpu": gpu["name"] if gpu else None}


def detect_hardware():
    memory = psutil.virtual_memory()
    info = dict(cpu=platform.processor() or "未知", cores=psutil.cpu_count(logical=False),
                threads=psutil.cpu_count(), ram_total=memory.total,
                ram_available=memory.available, gpus=[], gpu_error=None,
                vulkan_loader=False, onnx_providers=[])
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                info["cpu"] = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()
        except OSError:
            pass
        try:
            info["gpus"] = windows_adapters()
        except Exception as exc:
            info["gpu_error"] = str(exc)
        try:
            ctypes.WinDLL(os.path.join(os.environ["SystemRoot"], "System32", "vulkan-1.dll"))
            info["vulkan_loader"] = True
        except OSError:
            pass
    else:
        if sys.platform == "darwin":
            info["cpu"] = _macos_cpu()
            try:
                info["gpus"] = _macos_gpus()
            except Exception as exc:
                info["gpu_error"] = str(exc)
        else:
            info["gpu_error"] = "此检测页目前仅支持 Windows DXGI 与 macOS"
    try:
        import onnxruntime
        info["onnx_providers"] = onnxruntime.get_available_providers()
    except Exception:
        pass
    info["recommendation"] = recommend(info)
    return info


def format_hardware(info):
    lines = [
        f"CPU：{info['cpu']}",
        f"核心／线程：{info['cores'] or '未知'} / {info['threads'] or '未知'}",
        f"内存：{info['ram_total'] / GIB:.1f} GiB；当前可用 {info['ram_available'] / GIB:.1f} GiB",
        "",
    ]
    for index, gpu in enumerate(info["gpus"], 1):
        lines.append(f"显卡 {index}：{gpu['name']}（{gpu['vendor']}）")
        if gpu.get("dedicated_bytes"):
            lines.extend([
                f"  专用显存：{gpu['dedicated_bytes'] / GIB:.1f} GiB",
                f"  共享内存上限：{gpu['shared_bytes'] / GIB:.1f} GiB（不是空闲显存）",
            ])
        elif gpu.get("vram_text"):
            lines.append(f"  显存：{gpu['vram_text']}")
    if info["gpu_error"]:
        lines.append(f"显卡检测失败：{info['gpu_error']}")
    elif not info["gpus"]:
        lines.append("未检测到硬件显卡")
    lines.append("")
    if os.name == "nt":
        lines.append("Vulkan 驱动入口：" +
                     ("已发现，尚未验证模型推理" if info["vulkan_loader"] else "未发现"))
    lines.extend([
        "ONNX 运行库后端：" + ", ".join(info["onnx_providers"]),
        "",
        "建议：" + info["recommendation"]["reason"],
        "以上为容量建议，不是速度测试；未检测 GPU 空闲率或当前剩余显存。",
        "实际推理设备以启动日志为准。",
    ])
    return "\n".join(lines)
