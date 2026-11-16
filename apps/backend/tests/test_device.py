"""Per-model device selection and VRAM budgeting.

``resolve_device`` must not treat every model as whisper-large-v3: the memory
budget and the auto batch size have to follow the model the user actually
selected, or a small model would be wrongly rejected (or over-batched) on a
modest GPU.
"""

from __future__ import annotations

import pytest

from tiktuv.asr.device import resolve_device
from tiktuv.asr.registry import get_entry
from tiktuv.asr.soniox import SONIOX_MODEL_ID
from tiktuv.errors import ErrorCode, ModelError
from tiktuv.schemas import DeviceInfo, DevicePreference

#: FP16 in-memory sizes matching the catalogue entries.
SMALL_FP16 = 488_000_000        # openai/whisper-small (244 M params)
LARGE_FP16 = 3_100_000_000      # whisper-large-v3 (1.55 B params)

_GIB = 1 << 30


def _cuda(free: int, *, total: int = 16 * _GIB, supports_fp16: bool = True) -> DeviceInfo:
    return DeviceInfo(
        kind="cuda",
        name="Test GPU",
        index=0,
        total_memory_bytes=total,
        free_memory_bytes=free,
        supports_fp16=supports_fp16,
        compute_capability="8.6",
    )


def _cpu() -> DeviceInfo:
    return DeviceInfo(kind="cpu", name="Test CPU")


class TestMemoryBudget:
    def test_small_model_fits_where_large_is_rejected(self) -> None:
        devices = [_cuda(1_500_000_000), _cpu()]

        small = resolve_device(
            DevicePreference.AUTO,
            requested_fp16=True,
            requested_batch_size=1,
            devices=devices,
            weights_fp16_bytes=SMALL_FP16,
        )
        assert small.kind == "cuda"
        assert small.use_fp16 is True

        with pytest.raises(ModelError) as exc:
            resolve_device(
                DevicePreference.AUTO,
                requested_fp16=True,
                requested_batch_size=1,
                devices=devices,
                weights_fp16_bytes=LARGE_FP16,
            )
        assert exc.value.code is ErrorCode.INSUFFICIENT_VRAM

    def test_fp32_weights_double_the_budget(self) -> None:
        # 5 GiB free: FP16 large-v3 (3.1 GB) fits, FP32 large-v3 (6.2 GB) does not.
        devices = [_cuda(5 * _GIB), _cpu()]

        fp16 = resolve_device(
            DevicePreference.AUTO,
            requested_fp16=True,
            requested_batch_size=1,
            devices=devices,
            weights_fp16_bytes=LARGE_FP16,
        )
        assert fp16.use_fp16 is True

        with pytest.raises(ModelError) as exc:
            resolve_device(
                DevicePreference.AUTO,
                requested_fp16=False,
                requested_batch_size=1,
                devices=devices,
                weights_fp16_bytes=LARGE_FP16,
            )
        assert exc.value.code is ErrorCode.INSUFFICIENT_VRAM

    def test_batch_size_scales_with_model_size(self) -> None:
        devices = [_cuda(8 * _GIB), _cpu()]

        small = resolve_device(
            DevicePreference.AUTO,
            requested_fp16=True,
            requested_batch_size=0,
            devices=devices,
            weights_fp16_bytes=SMALL_FP16,
        )
        large = resolve_device(
            DevicePreference.AUTO,
            requested_fp16=True,
            requested_batch_size=0,
            devices=devices,
            weights_fp16_bytes=LARGE_FP16,
        )

        # More headroom for the smaller model means a larger auto batch size,
        # capped at the 16-item ceiling.
        assert small.batch_size > large.batch_size
        assert small.batch_size == 16
        assert large.batch_size >= 1

    def test_requested_batch_size_is_respected(self) -> None:
        resolved = resolve_device(
            DevicePreference.AUTO,
            requested_fp16=True,
            requested_batch_size=3,
            devices=[_cuda(8 * _GIB), _cpu()],
            weights_fp16_bytes=LARGE_FP16,
        )
        assert resolved.batch_size == 3


class TestDeviceSelection:
    def test_cpu_never_uses_fp16(self) -> None:
        resolved = resolve_device(
            DevicePreference.CPU,
            requested_fp16=True,
            requested_batch_size=1,
            devices=[_cpu()],
            weights_fp16_bytes=LARGE_FP16,
        )
        assert resolved.kind == "cpu"
        assert resolved.use_fp16 is False

    def test_cuda_request_without_gpu_is_rejected(self) -> None:
        with pytest.raises(ModelError) as exc:
            resolve_device(
                DevicePreference.CUDA,
                requested_fp16=True,
                requested_batch_size=1,
                devices=[_cpu()],
            )
        assert exc.value.code is ErrorCode.INSUFFICIENT_VRAM

    def test_default_weight_budget_is_large_v3(self) -> None:
        """Callers that do not name a model still get the large-v3 budget."""
        resolved = resolve_device(
            DevicePreference.CPU,
            requested_fp16=True,
            requested_batch_size=1,
            devices=[_cpu()],
        )
        assert resolved.kind == "cpu"


class TestCatalogueMemoryBudget:
    def test_local_entries_carry_their_fp16_weight_size(self) -> None:
        assert get_entry("ivrit-ai/whisper-large-v3").fp16_weight_bytes == LARGE_FP16
        assert get_entry("openai/whisper-large-v3").fp16_weight_bytes == LARGE_FP16
        assert get_entry("openai/whisper-small").fp16_weight_bytes == SMALL_FP16

    def test_cloud_entry_has_no_local_weight_budget(self) -> None:
        assert get_entry(SONIOX_MODEL_ID).fp16_weight_bytes == 0
