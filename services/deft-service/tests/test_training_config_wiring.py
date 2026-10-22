"""Every accepted config field must actually reach the trainer.

`TrainingConfig` uses `extra="forbid"`, so a misspelled key is a loud 422. That
makes the opposite failure invisible: a correctly spelled key that no code path
reads is accepted, validated, and silently ignored. `num_gpus` was in that state
-- requests set it, the schema approved it, and training ran single-GPU anyway
because the spec's own value won.

These tests are deliberately static. Driving the real train step needs a full
pipeline context (workspaces, CSVs, leakage checks), and the bug is not in that
machinery -- it is in the override list simply not mentioning a field.
"""

from __future__ import annotations

import inspect

from app.execution import aoi_pipeline
from app.schemas.config_models import EmbeddingConfig, TrainingConfig

#: Fields the trainer knowingly does not forward yet. Listing them here is not
#: an endorsement -- it is the visible debt. See
#: drafts/issues/deft-service-dead-training-config.md. Removing a name from this
#: set must be accompanied by wiring it up, and adding one needs a reason.
_KNOWN_UNWIRED = {"learning_rate", "freeze_backbone"}


def _pipeline_source() -> str:
    return inspect.getsource(aoi_pipeline)


def test_every_training_field_is_forwarded_or_explicitly_known_unwired():
    src = _pipeline_source()
    unread = {
        name
        for name in TrainingConfig.model_fields
        if f"training.{name}" not in src
    }
    assert unread == _KNOWN_UNWIRED, (
        "TrainingConfig fields neither forwarded to the trainer nor listed as "
        f"known-unwired: {sorted(unread - _KNOWN_UNWIRED)}. A field the schema "
        "accepts but nothing reads is silently ignored at runtime."
    )


def test_num_gpus_is_forwarded_as_a_tao_override():
    src = _pipeline_source()
    assert "train.num_gpus={ctx.cfg.training.num_gpus}" in src, (
        "train.num_gpus must be passed explicitly; without it the spec's own "
        "num_gpus wins and the request's value is ignored."
    )


def test_gpu_defaults_leave_headroom_for_inference():
    """The default must not claim a whole node.

    A box running training is usually also serving inference, so the default
    reserves capacity rather than taking every GPU. The previous value was 8 --
    a full node -- which would have starved inference the moment the field was
    honoured, and hard-failed outright on any node smaller than 8.
    """
    assert TrainingConfig().num_gpus == 4
    assert EmbeddingConfig().num_gpus == 4


def test_shipped_profiles_match_the_default():
    """Profiles target unknown hardware, so they should not out-claim the default."""
    from app.data.profiles import PROFILES

    default = TrainingConfig().num_gpus
    for name, profile in PROFILES.items():
        training = getattr(profile.config, "training", None)
        if training is not None:
            assert training.num_gpus <= default, (
                f"profile {name!r} requests {training.num_gpus} GPUs, more than the "
                f"default {default}; a profile is a guess about someone else's box"
            )
