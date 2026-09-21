"""Offline diagnostics for packaged builds; never records or types text."""

import argparse
import json
import sys
import traceback

from core.runtime_paths import APP_DIR, DATA_DIR


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--model", choices=("sensevoice", "paraformer", "fun_asr_nano", "qwen_asr"))
    parser.add_argument("--audio")
    args = parser.parse_args()
    report = {"python": sys.version, "app_dir": str(APP_DIR), "data_dir": str(DATA_DIR)}
    engine = None
    try:
        import tkinter as tk
        import onnxruntime
        import sounddevice
        from core.hotword_rules import encode_literal, parse_rules
        root = tk.Tk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        report["onnx_providers"] = onnxruntime.get_available_providers()
        report["input_devices"] = sum(
            device["max_input_channels"] > 0 for device in sounddevice.query_devices())
        pattern, replacement = parse_rules(encode_literal("a.b", r"C:\word"))[0]
        assert pattern.sub(replacement, "a.b") == r"C:\word"
        if args.model:
            from core.server.engines.factory import EngineFactory
            engine = EngineFactory.create_asr_engine(args.model)
            report["model"] = args.model
            if args.audio:
                import soundfile as sf
                audio, sample_rate = sf.read(args.audio, dtype="float32")
                if audio.ndim == 2:
                    audio = audio.mean(axis=1)
                stream = engine.create_stream()
                stream.accept_waveform(sample_rate, audio)
                engine.decode_stream(stream)
                report["text"] = stream.result.text
                if not report["text"].strip():
                    raise RuntimeError("Recognition returned empty text")
        report["ok"] = True
    except Exception:
        report["ok"] = False
        report["error"] = traceback.format_exc()
    finally:
        if engine is not None and hasattr(engine, "cleanup"):
            engine.cleanup()
    output = DATA_DIR / "logs" / "self-test.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1
