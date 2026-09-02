# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import logging

from .config import ConfigError, GatewayConfig
from .server import create_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = GatewayConfig.from_env()
    except ConfigError as error:
        raise SystemExit(f"configuration error: {error}") from error
    server = create_server(config)
    logging.getLogger(__name__).info(
        "VSS agent gateway listening on %s:%d using %s",
        config.bind_host,
        config.bind_port,
        config.backend_protocol,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
