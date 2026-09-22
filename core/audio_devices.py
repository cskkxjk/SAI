"""Shared input-device enumeration and persistent selection."""

import sounddevice as sd
import re

HOST_PRIORITY = {
    "Windows WASAPI": 0,
    "Windows DirectSound": 1,
    "MME": 2,
    "Windows WDM-KS": 3,
}


def input_devices():
    hosts = sd.query_hostapis()
    return [
        {"index": index, "name": device["name"],
         "hostapi": hosts[device["hostapi"]]["name"],
         "channels": int(device["max_input_channels"])}
        for index, device in enumerate(sd.query_devices())
        if device["max_input_channels"] > 0
    ]


def physical_input_devices(devices=None):
    """Collapse PortAudio host-api endpoints into physical microphone choices."""
    devices = input_devices() if devices is None else devices
    wasapi_devices = [
        device for device in devices
        if device["hostapi"] == "Windows WASAPI"
        and "microsoft sound mapper" not in device["name"].casefold()
    ]
    if wasapi_devices:
        devices = wasapi_devices
    else:
        devices = [
            device for device in devices
            if "microsoft sound mapper" not in device["name"].casefold()
        ]
    grouped = {}
    for device in devices:
        name = " ".join(device["name"].split()).casefold()
        # Windows adds host-specific endpoint prefixes such as "3- " to
        # otherwise identical microphone names.
        key = re.sub(r"(\(|\s)\d+\s*-\s*", r"\1", name)
        key = re.sub(r"@[^\)]*", "", key)
        current = grouped.get(key)
        if current is None or HOST_PRIORITY.get(device["hostapi"], 99) < HOST_PRIORITY.get(current["hostapi"], 99):
            grouped[key] = device
    return sorted(grouped.values(), key=lambda d: (
        HOST_PRIORITY.get(d["hostapi"], 99), d["name"].casefold()))


def resolve_input_device(selection, devices=None):
    if selection in (None, "", "default"):
        return None
    devices = input_devices() if devices is None else devices
    if isinstance(selection, int):
        matches = [d for d in devices if d["index"] == selection]
    elif isinstance(selection, dict):
        matches = [d for d in devices if d["name"] == selection.get("name")
                   and d["hostapi"] == selection.get("hostapi")]
        if len(matches) > 1:
            matches = [d for d in matches if d["index"] == selection.get("index")]
    else:
        raise ValueError("Invalid microphone selection; select the device again.")
    if len(matches) != 1:
        raise ValueError(f"Microphone unavailable or ambiguous: {selection}")
    return matches[0]["index"]


def resolve_capture_device(selection):
    """Use WASAPI for the default mic, never a different physical device."""
    selected = resolve_input_device(selection)
    if selected is not None:
        return selected
    default = sd.query_devices(kind="input")
    matches = [
        device for device in input_devices()
        if device["hostapi"] == "Windows WASAPI"
        and device["name"] == default["name"]
    ]
    return matches[0]["index"] if len(matches) == 1 else None
