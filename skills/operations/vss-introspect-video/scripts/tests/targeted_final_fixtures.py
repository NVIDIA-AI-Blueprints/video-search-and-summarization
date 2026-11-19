# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
def discriminator(claim_id="claim-color"):
    return {
        "claim_id": claim_id,
        "uncertain_fact": "Visible vehicle color",
        "alternative_observations": ["Blue body", "White body"],
        "distinguishing_observation": "A clear view of the body paint",
        "required_state": "During approach",
        "reference_frame": "Not applicable",
    }
