# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Redis Streams transport primitives.

Peer of :mod:`mdx.kafka_message_broker`: owns connection handling and the
raw XADD / XREADGROUP / XACK calls so the source and sink modules only deal
with Alert payload semantics.

Wire format on **publish** is the MDX stream envelope used by every other Redis
Streams producer and consumer in this repository (behavior-analytics, VIOS,
rt-cv-bev-fusion, the Logstash ``redis_stream`` input plugin)::

    XADD <stream> * key <sensorId> value <payload> headers <json>

``value`` carries the payload — protobuf bytes for the MDX schema streams.
Sticking to this envelope is what lets Alert MS read the incident and alert
streams that behavior-analytics writes, and lets Logstash read the
VLM-enhanced streams Alert MS writes.

On **read** two formats have to be decoded, and which field holds the event
body is a fixed contract rather than a guess — see
:data:`PAYLOAD_FIELD_PRECEDENCE`.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry

# Re-exported, not defined here. Both were moved out to keep this module about
# the connection: the wire format because it is a pure function of an entry's
# fields and pulled ``redis`` into every module that only wanted to read a dict,
# and credential resolution because it is a deployment concern any transport
# that authenticates would otherwise copy. Kept importable from here because
# that is where callers and functional tests already reach for them.
from mdx.redis_stream_envelope import (  # noqa: F401
    FALLBACK_PAYLOAD_FIELDS,
    HEADERS_FIELD,
    KEY_FIELD,
    PAYLOAD_FIELD,
    PAYLOAD_FIELD_PRECEDENCE,
    extract_envelope,
    message_id_to_epoch_ms,
)
from mdx.transport.secrets import resolve_secret

# Guarded so a minimal environment without the metrics package cannot break the
# publish path.
try:  # pragma: no cover - exercised indirectly
    from metrics import recorder as _metrics
except Exception:  # pragma: no cover
    _metrics = None

logger = logging.getLogger(__name__)

DEFAULT_HOST = "localhost"
DEFAULT_PORT = 6379
#: Trimming is **off** unless an operator asks for it. The streams Alert MS
#: publishes to belong to the deployment, not to Alert MS, and a MAXLEN on every
#: XADD makes normal successful output delete a customer's older entries — a
#: retention decision this service is in no position to make on their behalf.
#: Set ``redis.maxlen`` to opt in.
DEFAULT_MAXLEN = 0
#: What one unanswered Redis command costs, and with it every worst case above
#: this module: a publish spends it per attempt, shutdown spends it per stream it
#: releases, and a readiness probe on the consumer-group rebalance path spends it
#: inside the window the group allows a member before evicting it.
#:
#: Short for that reason. This was 30s, which is what made each of those a
#: minutes-long stall against a host that accepts packets and answers nothing —
#: and 30s also put it above ``publish_budget``, so the budget bounded nothing
#: (it is read between attempts, never mid-attempt). Every command this module
#: issues is a stream operation against an in-cluster instance, so seconds are
#: generous; a deployment that needs more can raise it, and one that raises it
#: past the budget is told.
DEFAULT_SOCKET_TIMEOUT = 5

#: Retrying is this module's job, so the client is told not to do any.
#:
#: Left unset, redis-py 6 applies ``Retry(ExponentialWithJitterBackoff(base=1,
#: cap=10), 3)`` to establishing a connection: a connect that times out is
#: retried four times, with a jittered sleep of up to ten seconds between the
#: attempts, *underneath* everything here. The layers multiply rather than
#: compose — measured on one command against a host that times out on connect,
#: four connect attempts and ten seconds of sleeping this module neither asked
#: for nor could see, so ``publish_retries``, ``publish_retry_backoff`` and
#: ``publish_budget`` were each describing the outer of two loops and a publish
#: cost minutes at the shipped timeouts.
#:
#: One attempt per command is also what makes the accounting true: a retry this
#: module did not make is not counted by it, so ``recovered`` undercounted.
_NO_CLIENT_RETRY = Retry(NoBackoff(), 0)
#: How long an entry must sit unacknowledged in a consumer's pending list before
#: another may claim it.
#:
#: The bound to clear is one *poll cycle*, not one VLM verification: every read
#: path acks each entry before returning it, so an entry is pending only between
#: ``XREADGROUP`` and ``XACK`` -- milliseconds -- and is still pending after that
#: only because the consumer died in between. Which is what the sweep is for, so
#: the threshold is what a stranded entry waits before anyone picks it up.
#:
#: It was five minutes, on the reasoning that a verification must finish first.
#: That reasoning does not apply to a window the verification is not inside, and
#: the cost of the excess is real: after a replica dies, its unacked entries wait
#: out the whole threshold before another consumer sees them.
DEFAULT_PENDING_MIN_IDLE_MS = 60_000
#: Config key for the above, and the two names it has had. Milliseconds, which is
#: why the canonical spelling says so -- ``*_time`` reads as seconds to anyone who
#: has met such a key before, and being wrong by a factor of a thousand here does
#: not announce itself.
PENDING_MIN_IDLE_KEY = "pending_min_idle_ms"
LEGACY_PENDING_MIN_IDLE_KEYS = ("reclaim_min_idle_ms", "reclaim_min_idle_time")

#: Publish retries attempted before a payload is dropped. A redisStream sink has
#: no second destination, so an XADD lost to a broker blip is an already-verified
#: verdict gone for good. Kept small: the caller is on the consume path and the
#: source has already acked, so blocking here stalls the batch behind it.
DEFAULT_PUBLISH_RETRIES = 2
DEFAULT_PUBLISH_RETRY_BACKOFF = 0.1
#: Wall-clock ceiling on one publish, retries included.
#:
#: A retry count alone does not bound the time a publish costs, because each
#: attempt can spend a whole socket timeout, and the caller is on the consume
#: path -- time spent here is time the source is not reading, for a destination
#: that has already said it is not there. One publish against a host that
#: blackholes packets measured 126.7s, and neither retry setting predicted it:
#: the client was retrying the connect four times underneath (see
#: :data:`_NO_CLIENT_RETRY`) and the timeouts were 30s.
#:
#: The ceiling only holds while a single attempt fits inside it, since it is read
#: between attempts and never mid-attempt -- interrupting an append would leave
#: one that may have landed indistinguishable from one that did not. That is what
#: :data:`DEFAULT_SOCKET_TIMEOUT` is sized against: 3 attempts x 5s is exactly
#: this budget, and a deployment that raises the timeout past it is warned at
#: startup rather than left with a ceiling that cannot apply.
DEFAULT_PUBLISH_BUDGET_SECONDS = 15.0
#: Floor under either socket timeout. Zero does not mean "no timeout" here: it
#: reaches ``socket.settimeout(0)``, which puts the socket in non-blocking mode
#: and fails every command immediately. Leaving the key unset is how a
#: deployment asks for the default.
MIN_SOCKET_TIMEOUT = 0.1

def resolve_redis_config(
    config: Dict[str, Any],
    section: Optional[str] = None,
    override: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Merge the top-level ``redis`` block with per-component overrides.

    Mirrors how Kafka resolves connection settings: the top-level ``redis``
    block holds the connection (the analogue of ``kafka.bootstrap_servers``)
    while ``event_bridge.redis_source`` / ``event_bridge.redis_sink`` hold the
    stream names and may override any connection field.

    Args:
        config: Full service configuration dictionary.
        section: Key under ``event_bridge`` to overlay, e.g. ``redis_source``.
        override: Explicit overlay applied last, for components that live
            outside ``event_bridge`` such as ``vlm_enhanced_sink``.

    A blank overlay value does not override. ``None`` never did, and an empty
    string is the same thing arriving from a rendered config: an unset variable
    substitutes as ``""``, so a per-component block that mentions ``host:`` and
    gets nothing for it used to erase a perfectly good top-level host and send
    the component to ``localhost``. Nothing legitimate is lost by the rule --
    ``host: ""`` and ``password: ""`` are not values, they are the absence of
    one, which is what falling through to the top-level block means.

    Returns:
        Merged settings dictionary. Never ``None``.
    """
    merged: Dict[str, Any] = dict(config.get("redis") or {})
    for overlay in ((config.get("event_bridge") or {}).get(section) if section else None, override):
        if overlay:
            merged.update({
                key: value for key, value in overlay.items()
                if value is not None
                and not (isinstance(value, str) and not value.strip())
            })
    return merged


def require_redis_endpoint(merged: Dict[str, Any], selected_by: str) -> str:
    """The host a resolved Redis config names, or raise saying it names none.

    :data:`DEFAULT_HOST` stays the constructor's default because
    :class:`RedisStreamBroker` is also built directly, by tests and by a
    developer poking at a local instance, and ``localhost`` is the right answer
    there. It is the wrong answer for a *deployment*: an unset ``REDIS_HOST``
    renders as ``""``, and falling back then means a service that was pointed at
    a customer's Redis instead consumes from a loopback address — polling
    forever on a source, because the source tolerates an unreachable broker by
    design, so nothing ever says why no events arrive.

    Called from the paths that build a component from deployment config, which
    is why the message names the transport selection that made a host
    mandatory: ``redis.host`` is only required because something asked for
    Redis, and the operator may not have realised that it did.
    """
    host = merged.get("host")
    if isinstance(host, str):
        host = host.strip()
    if not host:
        raise ValueError(
            f"redis.host is empty but {selected_by} selects a redisStream "
            f"transport. Alert MS does not deploy Redis, so point redis.host at "
            f"the instance you provide (REDIS_HOST under Compose, redis.host "
            f"under Helm). A blank host is almost always an unresolved variable "
            f"in a rendered config."
        )
    return str(host)


def require_redis_port(value: Any) -> int:
    """A usable TCP port from ``value``, or raise saying what is wrong.

    Absent or blank means :data:`DEFAULT_PORT`. Unlike the host, that is a safe
    thing to infer: 6379 is Redis's registered port, so a config that names a
    host and no port means the ordinary one.

    Anything else present is checked rather than passed through. It used to be
    ``int(value or DEFAULT_PORT)``, which sent ``70000`` and ``-1`` on to the
    client to fail as a connection error — reported against the address, which
    reads as "Redis is down" and sends the operator to look at Redis. And ``0``,
    which a template can produce, silently became 6379.

    A module-level function rather than a method for the same reason
    :func:`require_redis_endpoint` is one: the check is a pure predicate over
    config, so startup validation can run it before anything is constructed.
    Left on the broker, it only fired where the broker is built — inside a
    forked pipeline child, which crash-looped on a one-character typo instead of
    failing the container with the key to fix.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return DEFAULT_PORT

    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"redis.port must be a TCP port number, got {value!r}"
        ) from None

    if not 1 <= port <= 65535:
        raise ValueError(
            f"redis.port is {port}, which is not a TCP port (1-65535). "
            f"Leave it unset for the default ({DEFAULT_PORT})."
        )
    return port


def require_redis_db(value: Any) -> int:
    """A usable logical database number from ``value``, or raise saying what is wrong.

    Absent or blank means 0, the database a Redis client connects to when it says
    nothing — the same reasoning as the port's default.

    On the loud side of this module's line, with the host and the port, because it
    decides *where* entries are read and written and not how fast. ``db: "one"``
    coerced to 0 would connect to a database that exists, accept every command,
    and consume an empty stream in the wrong place — which reads as "the producer
    published nothing" and sends the operator to look at the producer.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0

    try:
        db = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError(
            f"redis.db must be a database number, got {value!r}"
        ) from None

    if db < 0:
        raise ValueError(
            f"redis.db is {db}; database numbers start at 0. Leave it unset for 0."
        )
    return db


def coerce_tuning(value: Any, default: Any, setting: str,
                  cast: Any = int, minimum: Any = 0) -> Any:
    """``value`` as a number at or above ``minimum``, or ``default`` and a line
    saying so.

    Every knob that tunes *how* a Redis component behaves reads through here,
    and the boundary that puts it in one place is worth naming: a value that
    decides **where** this connects fails the startup -- see
    :func:`require_redis_endpoint` and :func:`require_redis_port` -- while one
    that only tunes behaviour falls back, because a typo in a backoff is not
    worth refusing to start over.

    Falling back is not the same as ignoring, which is what the copies of this
    used to do: each swallowed an unparseable value silently, so
    ``pending_min_idle_ms: "one minute"`` ran at the default with nothing said
    and the operator's setting had no effect they could see.

    ``minimum`` is not always ``0``. A poll count of zero reads nothing and a
    backoff of zero does not pace anything, so for those the floor is what makes
    the knob's job survive a value that would otherwise disable it -- and it is
    a floor rather than a rejection for the same reason as above.

    ``setting`` is the whole dotted path, not a leaf, because the same knob name
    appears under ``redis`` and under a component's ``consumer_config`` and a
    warning naming only the leaf sends the operator to the wrong file.
    """
    try:
        coerced = cast(value)
    except (TypeError, ValueError):
        logger.warning(
            "%s is %r, which is not a number; using %s instead.",
            setting, value, default,
        )
        return default

    floor = cast(minimum)
    if coerced < floor:
        logger.warning(
            "%s is %r, which is below the %s this can run with; using %s "
            "instead.", setting, value, floor, floor,
        )
        return floor
    return coerced


#: Redis errors that arrive on the transport classes but still mean the command
#: was never run: both are refused during the handshake, before anything of ours
#: reached the server.
_NEVER_APPLIED = (
    redis.exceptions.AuthenticationError,
    redis.exceptions.BusyLoadingError,
)


def _may_have_landed(exc: BaseException) -> bool:
    """Whether ``exc`` leaves it unknown if the command was applied.

    Retrying can only duplicate what the server already applied, so what a
    retry costs is decided here rather than by the retry policy. A server that
    answered — even to refuse — did not append anything, and a connection that
    was never opened carried nothing; both make a retry a plain second attempt.
    A reply that never arrived is the case with no answer: the command went to
    a socket the server may well have read.

    Decided on the message and not only the class, because redis-py raises the
    same classes on both sides of the send: a connect that timed out is a
    ``TimeoutError`` exactly as a lost reply is, and only the text separates
    them. Anything unrecognized counts as *not* ambiguous, so this stays a
    statement about the failures Redis is known to raise instead of a guess
    about the rest.
    """
    if isinstance(exc, _NEVER_APPLIED):
        return False
    if isinstance(exc, (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)):
        return "connecting to" not in str(exc).lower()
    # A reply that arrived but could not be parsed was still a reply to a
    # command the server ran.
    return isinstance(exc, redis.exceptions.InvalidResponse)


#: Strings that mean "no value here" for the TLS switch, rather than a value.
#: ``""`` is the one that matters: a deployment config is rendered from
#: variables, and an unset one leaves the key present and empty.
_TLS_UNSET = ("", "null", "none", "~")
#: Strings that turn the TLS switch on, and the ones that turn it off. A value in
#: neither list is off *and* said out loud, because guessing either way about a
#: key named ``ssl`` is worse than reporting it.
_TLS_ON = ("true", "1", "yes", "on", "enabled")
_TLS_OFF = ("false", "0", "no", "off", "disabled")


def _asks_for_tls(value: Any, key: str) -> bool:
    """Whether ``key: value`` is asking for an encrypted connection.

    Presence, not truthiness, for the block form. ``tls: {}`` is a deployment
    writing the TLS section and meaning "on, with the defaults" -- an empty dict
    is falsy in Python, so reading this as a boolean ran that deployment
    unencrypted and said nothing. The scalar form keeps value semantics, because
    there ``ssl: ""`` is an unresolved variable rather than a request.
    """
    if isinstance(value, dict):
        if value:
            # The sub-keys are not read: the schema is flat (`ssl_ca_certs`,
            # `ssl_certfile`, ...) and always has been, so a nested spelling is
            # silently ignored -- the same class of quiet mistake as the one
            # above, at the level below it.
            logger.warning(
                "redis.%s is a mapping, so TLS is on, but %s under it are not "
                "read: the certificate settings are flat keys next to it "
                "(ssl_ca_certs, ssl_certfile, ssl_keyfile, ssl_cert_reqs)",
                key, sorted(value),
            )
        return True
    if value is None:
        return False
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TLS_UNSET or text in _TLS_OFF:
            return False
        if text in _TLS_ON:
            return True
        logger.warning(
            "redis.%s is %r, which is not a value this recognizes; TLS stays "
            "off. Use true or false.", key, value,
        )
        return False
    return bool(value)


def _resolve_tls_options(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Build the redis-py TLS keyword arguments, or ``{}`` when TLS is off.

    Verification is on by default once TLS is enabled: an encrypted connection
    that does not check the certificate protects against nothing an operator who
    asked for TLS was worried about. ``ssl_cert_reqs: none`` is available for a
    self-signed development instance and says so in the config rather than being
    the silent default.

    ``ssl`` and ``tls`` are two spellings of one switch; either one asking for
    TLS is enough. See :func:`_asks_for_tls` for what asking looks like.
    """
    if not any(
        _asks_for_tls(cfg[key], key) for key in ("ssl", "tls") if key in cfg
    ):
        return {}

    options: Dict[str, Any] = {
        "ssl": True,
        "ssl_cert_reqs": str(cfg.get("ssl_cert_reqs") or "required").lower(),
    }
    for key in ("ssl_ca_certs", "ssl_ca_path", "ssl_certfile", "ssl_keyfile"):
        value = cfg.get(key)
        if value:
            options[key] = value
    if options["ssl_cert_reqs"] != "none" and not (
        options.get("ssl_ca_certs") or options.get("ssl_ca_path")
    ):
        # Not an error: the system trust store is the right source for a
        # publicly-issued certificate. Worth saying, because a private CA that
        # was configured but not mounted fails at connect with a bare
        # verification error that does not name the missing setting.
        logger.info(
            "Redis TLS is enabled with certificate verification and no "
            "ssl_ca_certs; the system trust store will be used"
        )
    return options


class RedisStreamBroker:
    """Connection-managing wrapper around the Redis Streams commands.

    The client is created lazily and rebuilt after a connection error so a
    Redis restart does not require an Alert MS restart. Read failures are
    reported to the caller rather than raised, because the consume loop must
    survive a broker outage.
    """

    def __init__(self, redis_config: Dict[str, Any]) -> None:
        cfg = dict(redis_config or {})
        self.host: str = cfg.get("host") or DEFAULT_HOST
        self.port: int = require_redis_port(cfg.get("port"))
        self.db: int = require_redis_db(cfg.get("db"))
        self.password: Optional[str] = resolve_secret(cfg, "password")
        self.username: Optional[str] = cfg.get("username") or None
        self.tls: Dict[str, Any] = _resolve_tls_options(cfg)
        self.maxlen: Optional[int] = self._coerce_maxlen(cfg.get("maxlen", DEFAULT_MAXLEN))
        self.approximate_trim: bool = bool(cfg.get("approximate_trim", True))
        # Coerced, not passed through. redis-py compares these against a clock,
        # so a string reaches the socket layer intact and raises TypeError on the
        # first command -- outside every `redis.exceptions.*` handler in this
        # module, so it killed the process at the first poll rather than being
        # reported as a config mistake. A rendered config makes strings of
        # everything, which is how `socket_timeout: "30"` gets here.
        self._socket_timeout = coerce_tuning(
            cfg.get("socket_timeout", DEFAULT_SOCKET_TIMEOUT),
            DEFAULT_SOCKET_TIMEOUT, "redis.socket_timeout", cast=float,
            minimum=MIN_SOCKET_TIMEOUT,
        )
        self._socket_connect_timeout = coerce_tuning(
            cfg.get("socket_connect_timeout", DEFAULT_SOCKET_TIMEOUT),
            DEFAULT_SOCKET_TIMEOUT, "redis.socket_connect_timeout", cast=float,
            minimum=MIN_SOCKET_TIMEOUT,
        )
        self.publish_retries: int = self._coerce_retries(cfg.get("publish_retries", DEFAULT_PUBLISH_RETRIES))
        self.publish_retry_backoff: float = self._coerce_backoff(
            cfg.get("publish_retry_backoff", DEFAULT_PUBLISH_RETRY_BACKOFF)
        )
        self.publish_budget: float = self._coerce_tuning(
            cfg.get("publish_budget", DEFAULT_PUBLISH_BUDGET_SECONDS),
            DEFAULT_PUBLISH_BUDGET_SECONDS, "publish_budget", cast=float,
        )
        self.pending_min_idle_ms: int = self._coerce_idle_ms(
            self._read_pending_min_idle(cfg)
        )
        if 0 < self.publish_budget < self._socket_timeout:
            # The budget bounds *retries*: it is read between attempts, and a
            # single attempt runs for as long as the socket lets it. Below the
            # socket timeout it therefore cannot hold, and saying so here is
            # cheaper than measuring a publish that outlived its budget and
            # working back to why. On the module logger, like the coercion
            # warnings above: the instance's own is not set until below.
            logger.warning(
                "redis.publish_budget is %.1fs but redis.socket_timeout is "
                "%.1fs, so one failed attempt already outlasts the budget; "
                "lower socket_timeout to %.1fs or below for the budget to bound "
                "a publish",
                self.publish_budget, self._socket_timeout, self.publish_budget,
            )
        self._client: Optional[redis.Redis] = None
        self._ensured_groups: set = set()
        # None until the first command runs. Distinguishes "not tried yet" from
        # "tried and failed", which readiness needs: an unreachable broker
        # returns the same empty entry list as an idle stream, so without this
        # the source cannot tell the two apart and reports healthy either way.
        self._connection_ok: Optional[bool] = None
        # Cleared the first time the server says it has no XAUTOCLAIM, so a
        # pre-6.2 Redis costs one rejected command rather than one per sweep.
        self._autoclaim_supported: bool = True
        # The same, for the consumer-record housekeeping. Separate flag: an ACL
        # may allow XAUTOCLAIM and not XINFO, and losing the reclaim because the
        # cleanup was refused would trade a leak for stranded entries.
        self._consumer_admin_supported: bool = True
        self.logger = logging.getLogger(self.__class__.__name__)

    @property
    def connection_healthy(self) -> Optional[bool]:
        """Whether the last Redis command succeeded; ``None`` before the first."""
        return self._connection_ok

    def warn_if_block_exceeds_timeout(self, block_ms: float, setting: str) -> None:
        """Say so when a blocking read of ``block_ms`` cannot fit in one command.

        A blocking ``XREADGROUP`` is only answered once the block elapses, so a
        socket timeout at or under it makes every *idle* poll a timeout: the read
        is reported as a broker failure, the connection is marked unhealthy, and
        readiness flaps with nothing actually wrong. Two knobs in different
        sections, and the failure names neither.

        Here rather than at the caller because the timeout is this class's and is
        already coerced; a caller comparing against it would have to coerce the
        same value a second time and warn about it twice.
        """
        if block_ms / 1000 < self._socket_timeout:
            return
        self.logger.warning(
            "%s is %sms but redis.socket_timeout is %.1fs, so an idle poll will "
            "time out before the block ends and be read as a broker failure; "
            "raise socket_timeout above the block time",
            setting, block_ms, self._socket_timeout,
        )

    def _mark_connection(self, ok: bool) -> None:
        """Record the outcome of a command so readiness can read it.

        Logged only on a transition, because the consume loop calls this on
        every poll and a per-poll line would bury the change of state in it.
        """
        if self._connection_ok is ok:
            return
        if ok:
            self.logger.info("Redis at %s:%s is reachable", self.host, self.port)
        self._connection_ok = ok

    @staticmethod
    def _is_unsupported_command(message: str) -> bool:
        """Whether a ResponseError says the server does not have the command.

        Redis phrases this as ``ERR unknown command 'XAUTOCLAIM'``; a proxy in
        front of one may answer ``ERR unsupported command``. Matched narrowly on
        purpose — every other ResponseError means the command exists and was
        refused, which is a different problem with a different response.
        """
        lowered = message.lower()
        return "unknown command" in lowered or "unsupported command" in lowered

    @staticmethod
    def _coerce_maxlen(value: Any) -> Optional[int]:
        """Return a positive MAXLEN cap, or ``None`` to leave the stream untrimmed.

        Anything non-positive or unparseable means no trimming: the failure mode
        of guessing a cap is deleting a customer's records, and the failure mode
        of not trimming is a stream that grows until they set a policy on it.
        """
        try:
            maxlen = int(value)
        except (TypeError, ValueError):
            return None
        return maxlen if maxlen > 0 else None

    @staticmethod
    def _read_pending_min_idle(cfg: Dict[str, Any]) -> Any:
        """The configured idle threshold, under whichever name it is written.

        An older name keeps working, because a config using one is not wrong,
        only out of date. Each is warned about rather than silently accepted, so
        that a deployment gets renamed once instead of carrying two names for one
        setting forever.
        """
        if PENDING_MIN_IDLE_KEY in cfg:
            return cfg[PENDING_MIN_IDLE_KEY]
        for legacy in LEGACY_PENDING_MIN_IDLE_KEYS:
            if legacy in cfg:
                logger.warning(
                    "redis.%s is an old name for redis.%s and holds the same "
                    "milliseconds. Rename it; the old spelling is accepted only "
                    "so an existing config keeps working.",
                    legacy, PENDING_MIN_IDLE_KEY,
                )
                return cfg[legacy]
        return DEFAULT_PENDING_MIN_IDLE_MS

    @staticmethod
    def _coerce_tuning(value: Any, default: Any, setting: str,
                       cast: Any = int) -> Any:
        """:func:`coerce_tuning` for a key in the top-level ``redis`` block."""
        return coerce_tuning(value, default, f"redis.{setting}", cast=cast)

    @classmethod
    def _coerce_idle_ms(cls, value: Any) -> int:
        """Return a non-negative pending idle threshold in milliseconds."""
        return cls._coerce_tuning(
            value, DEFAULT_PENDING_MIN_IDLE_MS, PENDING_MIN_IDLE_KEY,
        )

    @classmethod
    def _coerce_retries(cls, value: Any) -> int:
        """Return a non-negative retry count; 0 disables retrying."""
        return cls._coerce_tuning(
            value, DEFAULT_PUBLISH_RETRIES, "publish_retries",
        )

    @classmethod
    def _coerce_backoff(cls, value: Any) -> float:
        """Return a non-negative backoff in seconds."""
        return cls._coerce_tuning(
            value, DEFAULT_PUBLISH_RETRY_BACKOFF, "publish_retry_backoff",
            cast=float,
        )

    def _publish_deadline(self) -> Optional[float]:
        """When the current publish must stop retrying, or ``None`` for never."""
        if self.publish_budget <= 0:
            return None
        return time.monotonic() + self.publish_budget

    def _out_of_budget(self, deadline: Optional[float], command: str) -> bool:
        """Whether ``deadline`` has passed, with a line saying so if it has.

        Checked between attempts rather than enforced on one: a socket timeout is
        the client's to enforce, and interrupting an attempt already in flight
        would leave an append that may have landed indistinguishable from one
        that did not. Between attempts the answer is unambiguous -- the last one
        failed -- and giving up there is what keeps a blackholed host from
        costing every verdict the full retry count times the connect timeout.
        """
        if deadline is None or time.monotonic() < deadline:
            return False
        self.logger.warning(
            "Giving up on this %s: %.1fs of publish_budget is spent and the "
            "caller is on the consume path, so retrying further would stall the "
            "source behind it",
            command, self.publish_budget,
        )
        return True

    def _record_publish_failure(self, outcome: str) -> None:
        """Report a failed publish attempt, if a metrics recorder is present."""
        if _metrics is None:
            return
        try:
            _metrics.inc_redis_publish_failure(outcome)
        except Exception:  # pragma: no cover - metrics must never break publish
            pass

    @property
    def client(self) -> redis.Redis:
        """Return the live client, creating it on first use."""
        if self._client is None:
            # decode_responses stays off: payloads are protobuf bytes.
            self._client = redis.Redis(
                host=self.host,
                port=self.port,
                db=self.db,
                username=self.username,
                password=self.password,
                decode_responses=False,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_connect_timeout,
                # See _NO_CLIENT_RETRY. This also replaces `retry_on_timeout`,
                # which redis-py 6 deprecated -- it never controlled how many
                # attempts were made, only whether a timeout joined the list of
                # errors the client's own retry applied to.
                retry=_NO_CLIENT_RETRY,
                **self.tls,
            )
            self.logger.info(
                "Redis Streams client configured for %s:%s (db=%s, tls=%s, "
                "auth=%s, trim=%s)",
                self.host, self.port, self.db,
                "on" if self.tls else "off",
                "on" if self.password else "off",
                self.maxlen if self.maxlen else "off",
            )
        return self._client

    def _reset_client(self) -> None:
        """Drop the client and any cached group state so the next call reconnects.

        Consumer groups are re-asserted after a reconnect because the Redis
        instance may have been replaced (or its data flushed) while we were
        disconnected.
        """
        self._ensured_groups.clear()
        client, self._client = self._client, None
        if client is None:
            return
        try:
            client.close()
        except Exception:  # pragma: no cover - best-effort teardown
            pass

    def ping(self) -> bool:
        """Verify connectivity. Returns ``False`` instead of raising."""
        try:
            self.client.ping()
            self._mark_connection(True)
            return True
        except Exception as exc:
            self.logger.error("Redis ping failed for %s:%s: %s", self.host, self.port, exc)
            self._mark_connection(False)
            self._reset_client()
            return False

    def ensure_group(self, stream: str, group: str, start_id: str = "$") -> bool:
        """Create the consumer group (and stream) if it does not exist.

        ``start_id`` defaults to ``$`` (new entries only) to match the Kafka
        source's ``auto_offset_reset: latest``. Pass ``0-0`` to replay history.
        """
        cache_key = (stream, group)
        if cache_key in self._ensured_groups:
            return True
        try:
            self.client.xgroup_create(stream, group, id=start_id, mkstream=True)
            self.logger.info("Created consumer group '%s' on stream '%s'", group, stream)
        except redis.exceptions.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                self.logger.error("Failed to create consumer group '%s' on '%s': %s", group, stream, exc)
                self._mark_connection(False)
                return False
            self.logger.debug("Consumer group '%s' already exists on '%s'", group, stream)
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis unavailable while creating group '%s' on '%s': %s", group, stream, exc)
            self._mark_connection(False)
            self._reset_client()
            return False

        self._mark_connection(True)
        self._ensured_groups.add(cache_key)
        return True

    def read_group(
        self,
        streams: Iterable[str],
        group: str,
        consumer: str,
        count: int,
        block_ms: int,
    ) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Read new entries for ``group`` across ``streams`` in one round trip.

        Returns:
            Flat list of ``(stream_name, message_id, fields)`` tuples. Empty on
            timeout or when the broker is unreachable.

        Raises:
            redis.exceptions.RedisError: never — connection and response errors
                are logged and surfaced as an empty result so the consume loop
                keeps running.
        """
        stream_list = [s for s in streams if s]
        if not stream_list:
            return []

        try:
            response = self.client.xreadgroup(
                groupname=group,
                consumername=consumer,
                streams={stream: ">" for stream in stream_list},
                count=count,
                block=block_ms,
            )
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while reading streams %s: %s", stream_list, exc)
            self._mark_connection(False)
            self._reset_client()
            return []
        except redis.exceptions.TimeoutError:
            # A blocking read that expires with nothing to hand back is the
            # normal idle case, not a broken connection.
            self.logger.debug("Redis read timed out with no new entries")
            self._mark_connection(True)
            return []
        except redis.exceptions.ResponseError as exc:
            # NOGROUP means the stream or group vanished (e.g. FLUSHDB); drop
            # the cache so the next poll recreates it. The connection itself is
            # fine, so it does not count against health.
            if "NOGROUP" in str(exc):
                self.logger.warning("Consumer group missing on read, will recreate: %s", exc)
                self._ensured_groups.clear()
                self._mark_connection(True)
            else:
                self.logger.error("Redis rejected XREADGROUP on %s: %s", stream_list, exc)
                self._mark_connection(False)
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis error while reading streams %s: %s", stream_list, exc)
            self._mark_connection(False)
            return []

        self._mark_connection(True)
        entries: List[Tuple[str, bytes, Dict[Any, Any]]] = []
        for stream_name, messages in response or []:
            if isinstance(stream_name, (bytes, bytearray)):
                stream_name = stream_name.decode("utf-8")
            for message_id, fields in messages or []:
                entries.append((stream_name, message_id, fields))
        return entries

    def ack(self, stream: str, group: str, message_ids: List[Any]) -> None:
        """Acknowledge ``message_ids`` so they leave the pending entries list.

        Retried on the same schedule as a publish, and with better justification:
        ``XACK`` is idempotent — acking an entry twice is acking it once — so a
        retry here cannot duplicate anything, where a publish retry can. What a
        lost ack does cost is the entry sitting in this consumer's pending list
        until the reclaim sweep hands it to another one, which then verifies it a
        second time and publishes a second verdict for it. That is the expensive
        path in this service, so a dropped ack is worth two more round trips.

        Failures are counted rather than only logged, under
        ``alert_bridge_redis_publish_failures_total{outcome="ack_dropped"}``:
        without a series, an ack that never landed was invisible until the
        duplicate verdict showed up somewhere downstream with nothing to tie it
        to.
        """
        if not message_ids:
            return
        attempts = self.publish_retries + 1
        deadline = self._publish_deadline()
        for attempt in range(1, attempts + 1):
            try:
                self.client.xack(stream, group, *message_ids)
            except redis.exceptions.RedisError as exc:
                if isinstance(exc, redis.exceptions.ConnectionError):
                    self._reset_client()
                self._mark_connection(False)
                if attempt < attempts and not self._out_of_budget(deadline, "ack"):
                    self.logger.warning(
                        "Failed to ack %s entries on '%s' (attempt %d/%d), "
                        "retrying: %s",
                        len(message_ids), stream, attempt, attempts, exc,
                    )
                    if self.publish_retry_backoff:
                        time.sleep(self.publish_retry_backoff * attempt)
                    continue
                self._record_publish_failure("ack_dropped")
                self.logger.error(
                    "Gave up acking %s entries on '%s' after %d attempt(s); "
                    "they stay pending and another consumer will re-verify "
                    "them: %s",
                    len(message_ids), stream, attempt, exc,
                )
                return

            self._mark_connection(True)
            if attempt > 1:
                self._record_publish_failure("ack_recovered")
                self.logger.warning(
                    "Acked %s entries on '%s' on attempt %d/%d",
                    len(message_ids), stream, attempt, attempts,
                )
            return

    def claim_stale(
        self,
        stream: str,
        group: str,
        consumer: str,
        count: int,
        min_idle_ms: Optional[int] = None,
    ) -> List[Tuple[str, bytes, Dict[Any, Any]]]:
        """Take over entries another consumer read but never acknowledged.

        ``XREADGROUP ... >`` only ever returns entries no one has seen. An entry
        delivered to a consumer that then died stays in that consumer's pending
        list forever: it is neither redelivered nor visible to a replacement,
        so without this pass a replica lost mid-batch strands its work with no
        upper bound. ``XAUTOCLAIM`` is the reclaim, gated on ``min_idle_ms`` so
        a consumer that is merely slow is not raced for entries it still owns.

        Returns:
            The claimed entries in the same ``(stream, message_id, fields)``
            shape as :meth:`read_group`, so the caller decodes them by one path.
            Empty when nothing is stale or the broker is unreachable.
        """
        if not self._autoclaim_supported:
            return []
        idle = self.pending_min_idle_ms if min_idle_ms is None else max(int(min_idle_ms), 0)
        try:
            response = self.client.xautoclaim(
                name=stream,
                groupname=group,
                consumername=consumer,
                min_idle_time=idle,
                start_id="0-0",
                count=count,
            )
        except redis.exceptions.ConnectionError as exc:
            self.logger.error("Redis connection lost while reclaiming on '%s': %s", stream, exc)
            self._mark_connection(False)
            self._reset_client()
            return []
        except redis.exceptions.ResponseError as exc:
            # Three different things arrive as a ResponseError here and they do
            # not mean the same thing, which is the whole point of splitting
            # them: treating all of them as benign left a permission error
            # looking exactly like an old server, so reclaim was disabled and
            # readiness stayed green with only a repeating warning to say so.
            message = str(exc)
            if "NOGROUP" in message:
                # Same meaning as on the read path: the group vanished. The
                # connection is fine and the next poll recreates it.
                self._ensured_groups.clear()
                self._mark_connection(True)
            elif self._is_unsupported_command(message):
                # Redis < 6.2 has no XAUTOCLAIM. A deployment fact, not a
                # fault — so it must neither flap health nor be retried, and it
                # is said once rather than on every sweep.
                self._autoclaim_supported = False
                self.logger.warning(
                    "This Redis does not support XAUTOCLAIM (needs 6.2+), so "
                    "entries stranded by a dead consumer will not be reclaimed. "
                    "Reclaim is now disabled for the life of this process: %s",
                    exc,
                )
                self._mark_connection(True)
            else:
                # WRONGTYPE, NOPERM/ACL, OOM: the server is reachable but is
                # refusing a command this consumer needs, which is an operator
                # problem and has to reach readiness rather than sit in the log.
                self.logger.error(
                    "Redis refused XAUTOCLAIM on '%s'; stale entries will not be "
                    "reclaimed and this is not a version limitation: %s",
                    stream, exc,
                )
                self._mark_connection(False)
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.error("Redis error while reclaiming on '%s': %s", stream, exc)
            self._mark_connection(False)
            return []

        self._mark_connection(True)
        # Redis 6.2 answers (cursor, entries); 7.0 added a third element listing
        # ids that no longer exist. Index rather than unpack so one server
        # version is not a TypeError on the consume path.
        messages = response[1] if isinstance(response, (list, tuple)) and len(response) > 1 else []
        entries: List[Tuple[str, bytes, Dict[Any, Any]]] = []
        for message_id, fields in messages or []:
            # XAUTOCLAIM reports tombstoned ids as (id, None) on some versions;
            # they carry nothing to process but must still be acked, which the
            # caller does from the id.
            entries.append((stream, message_id, fields or {}))
        if entries:
            self.logger.warning(
                "Reclaimed %d entry(ies) on '%s' left pending for more than %dms "
                "by another consumer in group '%s'",
                len(entries), stream, idle, group,
            )
        return entries

    def list_consumers(self, stream: str, group: str) -> List[Dict[str, Any]]:
        """Every consumer record in ``group``, with its pending count and idle time.

        A group keeps a record per consumer *name* that has ever read from it,
        and nothing expires them: unlike a Kafka group, where membership is a
        session the broker times out, a Redis consumer is created by being named
        and removed only by ``XGROUP DELCONSUMER``.

        Returns:
            ``[{"name": str, "pending": int, "idle": int}, ...]``, or ``[]`` when
            the group is unknown or the server will not answer. Never raises and
            never touches the connection health flag: this serves the hygiene
            sweep, and a group that cannot be inspected is not a reason to report
            a pipeline unready when reading and acking are working.
        """
        if not self._consumer_admin_supported:
            return []
        try:
            records = self.client.xinfo_consumers(stream, group)
        except redis.exceptions.ResponseError as exc:
            message = str(exc)
            if "NOGROUP" in message:
                self._ensured_groups.clear()
            elif self._is_unsupported_command(message):
                self._consumer_admin_supported = False
                self.logger.info(
                    "This Redis does not answer XINFO CONSUMERS, so dead "
                    "consumer records will not be cleaned up: %s", exc,
                )
            else:
                # An ACL without XINFO is the usual cause. Said once, at the
                # level of the housekeeping it disables rather than as an error
                # about the consume path, which is unaffected.
                self._consumer_admin_supported = False
                self.logger.warning(
                    "Redis refused XINFO CONSUMERS on '%s', so dead consumer "
                    "records will not be cleaned up for the life of this "
                    "process: %s", stream, exc,
                )
            return []
        except redis.exceptions.RedisError as exc:
            self.logger.debug(
                "Could not list consumers on '%s': %s", stream, exc,
            )
            return []

        consumers: List[Dict[str, Any]] = []
        for record in records or []:
            decoded = {
                (k.decode("utf-8") if isinstance(k, (bytes, bytearray)) else k): v
                for k, v in dict(record).items()
            }
            name = decoded.get("name")
            if isinstance(name, (bytes, bytearray)):
                name = name.decode("utf-8", errors="replace")
            consumers.append({
                "name": name,
                "pending": int(decoded.get("pending") or 0),
                "idle": int(decoded.get("idle") or 0),
            })
        return consumers

    def delete_consumer(self, stream: str, group: str, consumer: str) -> Optional[int]:
        """Remove one consumer record from ``group``.

        Returns:
            How many pending entries the removal discarded, or ``None`` if the
            command did not run. **Discarded, not reclaimed** — ``XGROUP
            DELCONSUMER`` deletes the consumer's pending entries along with it,
            so a caller must establish that there are none before calling this.
            :meth:`list_consumers` is how.
        """
        if not self._consumer_admin_supported:
            return None
        try:
            return int(self.client.xgroup_delconsumer(stream, group, consumer))
        except redis.exceptions.RedisError as exc:
            self.logger.debug(
                "Could not remove consumer '%s' from group '%s' on '%s': %s",
                consumer, group, stream, exc,
            )
            return None

    def add(
        self,
        stream: str,
        payload: bytes,
        key: Any = b"",
        headers: Optional[Dict[str, Any]] = None,
    ) -> Optional[bytes]:
        """Publish ``payload`` to ``stream`` using the MDX envelope.

        A failed XADD is retried up to ``publish_retries`` times with a linear
        backoff, because a redisStream sink is the only destination for the
        payload: unlike the read path, there is nothing upstream that will hand
        it to us again. Retries are deliberately few — the caller runs on the
        consume path and the source has already acked.

        **A retry can append a second copy, and that is the chosen trade.** A
        failure with no reply — a connection lost or timed out after the command
        was written — does not say whether the server appended the entry, and
        Redis offers nothing to ask with: an entry ID is assigned by the server,
        and an ID chosen here to make the append idempotent would be refused
        whenever another writer got in first, turning a rare duplicate into a
        silent loss. So the ambiguous case is retried and counted under
        ``...{outcome="replayed"}``, which is the upper bound on the duplicates
        a reader of this stream may have seen. Failures that cannot have applied
        anything — a refusal from the server, a connection never opened — are
        retried without that count; see :func:`_may_have_landed`.

        Trimming is applied only when ``maxlen`` is configured; see
        :data:`DEFAULT_MAXLEN` for why it is off by default.

        Returns:
            The generated entry ID, or ``None`` if every attempt failed. A
            ``None`` return means the payload was dropped and is counted under
            ``alert_bridge_redis_publish_failures_total{outcome="dropped"}``.
            Callers that have no second destination must treat it as an error
            rather than a completed write.
        """
        return self._add(stream, payload, key, headers, self._publish_deadline())

    def _add(
        self,
        stream: str,
        payload: bytes,
        key: Any,
        headers: Optional[Dict[str, Any]],
        deadline: Optional[float],
    ) -> Optional[bytes]:
        """:meth:`add` against a caller-supplied deadline.

        Split out so a batch falling back to one publish per entry can share a
        single budget across all of them. Given one deadline each, a ten-entry
        batch against a host that blackholes packets costs ten budgets — the
        multiplication the budget exists to stop, one level up.
        """
        if isinstance(key, str):
            key = key.encode("utf-8")
        elif key is None:
            key = b""

        fields = {
            KEY_FIELD: key,
            PAYLOAD_FIELD: payload,
            HEADERS_FIELD: json.dumps(headers or {}),
        }

        kwargs: Dict[str, Any] = {}
        if self.maxlen is not None:
            kwargs["maxlen"] = self.maxlen
            kwargs["approximate"] = self.approximate_trim

        attempts = self.publish_retries + 1
        for attempt in range(1, attempts + 1):
            try:
                entry_id = self.client.xadd(stream, fields, **kwargs)
            except redis.exceptions.RedisError as exc:
                # ConnectionError is a RedisError subclass; only a broken
                # connection warrants rebuilding the client before retrying.
                if isinstance(exc, redis.exceptions.ConnectionError):
                    self._reset_client()
                self._mark_connection(False)
                if attempt < attempts and not self._out_of_budget(deadline, "publish"):
                    if _may_have_landed(exc):
                        # The command was written to a socket and no reply came
                        # back, so the entry may be in the stream already and
                        # this retry appends a second copy. Retried anyway: the
                        # sink has no second destination, and a verdict that
                        # never landed cannot be reconstructed while a duplicate
                        # one can be recognized. Counted so the duplicate has a
                        # timestamp to be tied back to -- see add_batch.
                        self._record_publish_failure("replayed")
                        self.logger.warning(
                            "Redis write to '%s' failed after the command was sent "
                            "(attempt %d/%d), so the entry may already be in the "
                            "stream; retrying rather than losing one that may never "
                            "have landed: %s",
                            stream, attempt, attempts, exc,
                        )
                    else:
                        self.logger.warning(
                            "Redis write to '%s' failed (attempt %d/%d), retrying: %s",
                            stream, attempt, attempts, exc,
                        )
                    if self.publish_retry_backoff:
                        time.sleep(self.publish_retry_backoff * attempt)
                    continue
                self._record_publish_failure("dropped")
                self.logger.error(
                    "Dropping payload after %d failed Redis write(s) to '%s': %s",
                    attempt, stream, exc,
                )
                return None

            self._mark_connection(True)
            if attempt > 1:
                self._record_publish_failure("recovered")
                self.logger.warning(
                    "Redis write to '%s' succeeded on attempt %d/%d", stream, attempt, attempts
                )
            return entry_id

        return None  # pragma: no cover - loop always returns

    def add_batch(
        self,
        stream: str,
        entries: List[Tuple[bytes, Any]],
        headers: Optional[Dict[str, Any]] = None,
    ) -> List[Optional[bytes]]:
        """Publish several payloads to one stream in a single round trip.

        Same envelope and same trimming as :meth:`add`, one network exchange
        instead of *n*. That matters because the caller is on the consume path:
        publishing a batch of ten entries one at a time spends ten round trips
        of latency there, and the source cannot read the next batch until it
        returns. The pipeline is not transactional — these are independent
        appends and a MULTI would only add a failure mode where one bad entry
        discards the rest.

        Retries are deliberately not attempted per entry here. A pipeline that
        fails has usually failed as a whole (a dropped connection), so on any
        error this falls back to :meth:`add` for each entry, which is where the
        retry, backoff and drop accounting live. The cost of that fallback is
        the slow path only.

        **That fallback can duplicate, and the choice is deliberate.**
        ``execute`` writes every command and then reads every reply, so a
        connection lost between the two leaves entries the server has already
        appended with nothing to say so — and re-publishing the batch appends
        them a second time. The three options are all lossy in some way:
        dropping the batch loses entries that may never have landed, a
        ``MULTI``/``EXEC`` narrows the window but lets one rejected entry discard
        the rest, and re-publishing risks a duplicate. Duplication is the one
        chosen, because the sink on this path has no second destination and a
        lost validation-error response cannot be reconstructed, while a duplicate
        one can be recognised.

        Recognised, not collapsed automatically: the MDX envelope carries no
        idempotency key, and adding one would change what *every* consumer of
        these streams sees, for a failure this rare. So the replay is counted
        instead — ``alert_bridge_redis_publish_failures_total{outcome="replayed"}``
        — and logged with the batch size, which is what lets a duplicate found
        downstream be tied back to the moment it was made.

        Returns:
            One entry ID per input, in order, with ``None`` where that entry
            was dropped — same contract as :meth:`add`, so a caller checks the
            list rather than trusting the call.
        """
        if not entries:
            return []
        # One budget for the whole batch, so the fallback below cannot spend one
        # per entry.
        deadline = self._publish_deadline()
        if len(entries) == 1:
            # One append needs no pipeline. It is not free of the duplicate
            # above -- a single lost reply is ambiguous the same way -- but
            # add() decides that per attempt, and one entry cannot leave a
            # batch half applied.
            payload, key = entries[0]
            return [self._add(stream, payload, key, headers, deadline)]

        kwargs: Dict[str, Any] = {}
        if self.maxlen is not None:
            kwargs["maxlen"] = self.maxlen
            kwargs["approximate"] = self.approximate_trim

        encoded_headers = json.dumps(headers or {})
        try:
            pipe = self.client.pipeline(transaction=False)
            for payload, key in entries:
                if isinstance(key, str):
                    key = key.encode("utf-8")
                elif key is None:
                    key = b""
                pipe.xadd(
                    stream,
                    {
                        KEY_FIELD: key,
                        PAYLOAD_FIELD: payload,
                        HEADERS_FIELD: encoded_headers,
                    },
                    **kwargs,
                )
            results = pipe.execute(raise_on_error=False)
        except redis.exceptions.RedisError as exc:
            if isinstance(exc, redis.exceptions.ConnectionError):
                self._reset_client()
            self._mark_connection(False)
            self._record_publish_failure("replayed")
            self.logger.warning(
                "Pipelined write of %d entries to '%s' failed after the commands "
                "were sent, so any the server already appended will be appended "
                "again; re-publishing all %d individually rather than losing the "
                "ones that did not land: %s",
                len(entries), stream, len(entries), exc,
            )
            return [
                self._add(stream, payload, key, headers, deadline)
                for payload, key in entries
            ]

        # raise_on_error=False puts a per-command failure in the results list
        # rather than aborting, so one rejected entry does not cost the others
        # their round trip. Those are retried individually.
        self._mark_connection(True)
        entry_ids: List[Optional[bytes]] = []
        for (payload, key), result in zip(entries, results):
            if isinstance(result, Exception):
                self.logger.warning(
                    "Redis rejected one pipelined entry on '%s', retrying it "
                    "on its own: %s", stream, result,
                )
                entry_ids.append(self._add(stream, payload, key, headers, deadline))
            else:
                entry_ids.append(result)
        return entry_ids

    def close(self) -> None:
        """Release the connection."""
        if self._client is None:
            return
        try:
            self._client.close()
            self.logger.info("Redis Streams connection closed")
        except Exception as exc:  # pragma: no cover - best-effort teardown
            self.logger.error("Error closing Redis connection: %s", exc)
        finally:
            self._client = None
            self._ensured_groups.clear()
