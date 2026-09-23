"""Recommendation boundaries and explicit opt-in GUI configuration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.hardware_info import GIB, merge_duplicate_gpus, recommend, format_hardware


def hardware(vram=8, vendor=0x1002, ram=32, free=16, vulkan=True):
    return dict(
        cpu="Test CPU", cores=8, threads=16, ram_total=ram * GIB, ram_available=free * GIB,
        gpus=[dict(name="Test GPU", vendor_id=vendor, vendor="Test",
                   dedicated_bytes=vram * GIB, shared_bytes=16 * GIB)],
        gpu_error=None, vulkan_loader=vulkan, onnx_providers=["CPUExecutionProvider"])


class RecommendationTests(unittest.TestCase):
    def test_amd_nvidia_intel_dedicated(self):
        for vendor in (0x1002, 0x10DE, 0x8086):
            result = recommend(hardware(vendor=vendor))["settings"]
            self.assertEqual(result["model_type"], "qwen_asr")
            self.assertEqual(result["qwen_quantization"], "q5_k")
            self.assertTrue(result["llm_use_gpu"])

    def test_duplicate_adapters_are_merged(self):
        gpu = dict(name="RTX 4070", vendor_id=0x10DE, vendor="NVIDIA",
                   dedicated_bytes=8 * GIB, shared_bytes=16 * GIB)
        duplicated = [dict(gpu), dict(gpu, shared_bytes=32 * GIB),
                      dict(name="Intel Iris Xe", vendor_id=0x8086, vendor="Intel",
                           dedicated_bytes=128 << 20, shared_bytes=16 * GIB)]
        merged = merge_duplicate_gpus(duplicated)
        self.assertEqual(len(merged), 2)
        self.assertEqual([item["name"] for item in merged],
                         ["RTX 4070", "Intel Iris Xe"])
        self.assertEqual(merged[0]["shared_bytes"], 32 * GIB)

        info = hardware()
        info["recommendation"] = recommend(info)
        self.assertNotIn("预加速", format_hardware(info))

    def test_small_dedicated_gpu_q4(self):
        self.assertEqual(recommend(hardware(vram=3))["settings"]["qwen_quantization"], "q4_k")

    def test_shared_memory_not_counted_as_dedicated(self):
        info = hardware(vram=0.5)
        info["gpus"][0]["shared_bytes"] = 128 * GIB
        self.assertEqual(recommend(info)["settings"]["model_type"], "fun_asr_nano")

    def test_no_gpu(self):
        info = hardware()
        info["gpus"] = []
        self.assertFalse(recommend(info)["settings"]["llm_use_gpu"])

    def test_no_vulkan_or_low_memory(self):
        for info in (hardware(vulkan=False), hardware(ram=4), hardware(free=2)):
            self.assertEqual(recommend(info)["settings"]["model_type"], "sensevoice")

    def test_virtual_adapter_not_recommended(self):
        self.assertEqual(recommend(hardware(vendor=0x1414))["settings"]["model_type"], "sensevoice")

    def test_failure_not_reported_as_no_gpu(self):
        info = hardware()
        info["gpu_error"] = "DXGI failed"
        info["recommendation"] = recommend(info)
        self.assertIn("检测失败", format_hardware(info))
        self.assertIn("检测失败不等于没有显卡", info["recommendation"]["reason"])

    def test_hybrid_prefers_dedicated_for_advice(self):
        info = hardware(vram=0.5)
        bigger = hardware(vram=12)["gpus"][0]
        bigger["name"] = "dedicated"
        info["gpus"].append(bigger)
        self.assertEqual(recommend(info)["gpu"], "dedicated")


class HardwareGuiTests(unittest.TestCase):
    def test_detection_does_not_change_settings_and_apply_is_explicit(self):
        import gui_launcher as gui
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(gui, "CONFIG", Path(directory) / "config_gui.json"), \
                patch.object(gui.Launcher, "_start_tray", lambda self: setattr(self, "tray_icon", None)), \
                patch.object(gui.Launcher, "_refresh_audio_devices", lambda self: None), \
                patch.object(gui.Launcher, "_detect_hardware", lambda self: None):
            window = gui.Launcher()
            try:
                window.geometry("700x780")
                window.update()
                before = window._data()
                info = hardware(vram=3)
                info["recommendation"] = recommend(info)
                window.hardware_events.put((info, None))
                window._poll_hardware()
                self.assertEqual(window._data(), before)
                with patch.object(gui.messagebox, "askokcancel", return_value=True):
                    window._apply_hardware()
                self.assertEqual(window.vars["qwen_quantization"].get(), "q4_k")
                self.assertFalse(gui.CONFIG.exists())
                self.assertEqual(window.vars["audio_device"].get(), gui.DEFAULT_MIC)
                self.assertLessEqual(
                    window.start_button.winfo_rooty() + window.start_button.winfo_height(),
                    window.winfo_rooty() + window.winfo_height())
            finally:
                window.destroy()


if __name__ == "__main__":
    unittest.main()
